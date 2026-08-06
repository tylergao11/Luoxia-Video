"""Three-agent production orchestration over the existing Luoxia contracts.

There are exactly three creative owners:

- language: story selection, explosive beats and final dialogue text;
- voice: casting, performance direction and measured audio;
- visual: appearance, coverage, prompts, stills and video.

They do not exchange private side documents.  Every accepted decision is written back to
the shared beats document, then bridged once into the shared audio-first timeline.  The
assembler remains deterministic and is intentionally not a fourth creative agent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from src.audio.performance import normalize_performance
from src.luoxia.beats.analyzer import analyze_novel
from src.luoxia.beats.selector import (
    finalize_selection,
    plan_episodes,
    prepare_selection,
)
from src.luoxia.beats.to_timeline import build_timeline_draft
from src.luoxia.beats.validator import (
    RETAINED,
    coverage_budget,
    coverage_settings,
    validate_beats,
)
from src.luoxia.llm.client import LuoxiaLLM
from src.luoxia.render.runner import render_timeline_videos
from src.luoxia.rewrite import make_rewrite_fn
from src.luoxia.speech import (
    configured_voice_records,
    make_tts_synthesize,
    voices_for_gender,
)
from src.luoxia.stills.characters import ensure_character_sheets
from src.luoxia.stills.prompts import polish_timeline_prompts
from src.luoxia.stills.runner import render_timeline_stills
from src.luoxia.timeline.solver import RewriteFn, solve_timeline
from src.luoxia.timeline.transitions import DISSOLVE, head_room_s, tail_room_s
from src.luoxia.timeline.validator import validate_timeline


VOICE_CAST_SYSTEM = """你是短剧声音导演，只负责角色音色选择。不得改角色、剧情或台词。
结合角色的阵营、故事语境和台词气质，从给定 voice_catalog 的 name、gender、scene、provenance、description 等真实元数据中为每个角色选一个 voice_id。主要角色尽量避免撞音色；同一角色全片只能有一个音色。不要只按角色数组顺序机械分配。
只输出 JSON：{"cast":[{"character_id":"...","voice_id":"..."}]}。"""

VOICE_PERFORMANCE_SYSTEM = """你是短剧台词表演导演，只负责语气、停顿、重音和情绪递进。不得改动任何一个台词字符，也不得决定镜头。
每句台词用 beat_type、intensity 和上下文判断表演弧。不要堆叠形容词，不要每个短语都重新起势；优先用一个连续 style，明确转折时最多两段，全句最多一个 event_before。
输入中的每一句台词都必须返回一条有效 direction；不得漏句，不得用空 performance 交差。
performance.segments[].text 必须逐字复制台词中的连续片段。
style 只能是 soft, whisper, loud, build-intensity, decrease-intensity, higher-pitch, lower-pitch, slow, fast, laugh-speak, emphasis 或 null。
event_before 只能是 pause, long-pause, breath, inhale, exhale, sigh, chuckle 或 null。
只输出 JSON：{"lines":[{"beat_id":"...","line_index":0,"delivery":"整句表演意图","performance":{"intent":"...","segments":[{"text":"原台词连续片段","style":"build-intensity","event_before":null}]}}]}。"""

VISUAL_CAST_SYSTEM = """你是短剧视觉导演，负责角色可复用定妆。不得选择音色，不得改剧情或台词。
为每个非旁白角色写长期固定外形：年龄感、脸型骨相、发型、服装、材质和气质。禁止写当前表情、动作和镜头。
只输出 JSON：{"cast":[{"character_id":"...","appearance":"..."}]}。"""

VISUAL_COVERAGE_SYSTEM = """你是短剧视觉与剪辑导演。语言 Agent 已锁定剧情和台词，你只能决定如何让爆点被看见：景别、无台词反应/插入/动作镜头、构图和非均匀节奏。

硬规则：
1. 不得改动、增删或复述台词；不得选择音色或语气。
2. line_shot_sizes 与原台词逐项对应，只能用 extreme_close_up, close_up, medium, full, wide, insert。短剧优先 medium，只有关键反应才 close_up，禁止全程贴脸。
3. visuals 只允许 establishing, reaction, insert, action；after_line=0 表示台词前，k 表示第 k 句之后。每个 visual 是一次独立的视频生成，也是一个完整电影镜头；最终由 assembler 按列表顺序组合。reaction 必须指定 subject；characters 列出本镜真正可见的角色，reaction 只列 subject。
4. 复杂事件按机位和叙事作用拆成少量完整镜头，不要拆成人体动作的微小状态。比如大招可用三镜：中景蓄力并发出（3 秒）→追踪飞行并看到敌人防御（3 秒）→命中、爆炸和余波（4 秒）。每镜内部由视频模型完成连贯动作弧。
5. prompt 只用正向语言写清本镜的起始画面、人物身体动作、结果画面和摄影机运动。优先描述脚步、重心、髋肩、手臂、受力与位移，再写光效；不要用一串禁止项代替导演意图。
6. 时长必须服务爆点，禁止机械均分。完整动作镜头通常 2-4 秒；弱镜短，命中可以瞬时，爆炸余波可以更长。action_duration_s 为 1-15 秒。
7. 必须服从 shot_budget 与 silent_shot_budget。高强度优先保留发出、追踪/反应和命中结果，铺垫尽量一镜带过，不为凑数增加镜头。
8. 对峙场景的关键台词之后，优先补对手或旁观者的 reaction，让表情变化真正出现在成片里；不要假设背景人物会自动表演。
9. 输入中的每个 beat 都必须返回一条方向。只输出 JSON：{"beats":[{"beat_id":"...","line_shot_sizes":[],"visuals":[{"kind":"action","after_line":0,"subject":null,"characters":["hero","enemy"],"scene_id":"...","shot_size":"medium","prompt":"三秒完整中景：英雄蓄力后转髋蹬地，双掌把能量体猛烈发出，身体清楚后坐，镜头跟随发射方向","action_duration_s":3},{"kind":"action","after_line":0,"subject":null,"characters":["hero","enemy"],"scene_id":"...","shot_size":"wide","prompt":"三秒完整追踪镜头：能量体高速飞越战场，敌人发现攻击并主动架起防御","action_duration_s":3},{"kind":"action","after_line":0,"subject":null,"characters":["hero","enemy"],"scene_id":"...","shot_size":"wide","prompt":"四秒完整冲击镜头：攻击正面命中，敌人受力后退，爆炸扩张后拉远展示余波","action_duration_s":4}]}]}。
"""

TIMELINE_TRANSITION_SYSTEM = """你是短剧剪辑导演。声音 Agent 已锁定每个镜头的真实时长；你只设计镜头之间的转场，不得改变镜头顺序、台词、时长或提示词。

硬规则：
1. 绝大多数边界使用 cut。正反打、动作接反应、连续对话必须硬切，不能滥用柔化转场。
2. fade_black 只用于明确的场景或时间跳转；fade_white 只用于闪回、强光或冲击峰值；dissolve 只用于同场景内时间流逝。
3. 转场属于当前镜头切向下一镜头。最后一个镜头必须 cut。
4. duration_s 只能是 0-1.5 秒。程序会按已锁定的无台词留白再次收紧；不得用转场掩盖台词。
5. 不要平均分配转场。爆点靠蓄压、瞬间硬切、可读反应和余威形成，而不是每隔固定秒数做一次效果。
6. 这些镜头已经分别生成；转场只负责把完整镜头接起来。连续动作、动作接反应和命中前后优先 cut，不得用转场掩盖镜头内容。
7. 只输出 JSON：{"shots":[{"shot_id":"...","kind":"cut","duration_s":0,"note":"选择理由"}]}。
"""


class AgentStageError(RuntimeError):
    pass


class LanguageAgentPort(Protocol):
    def adapt(
        self,
        text: str,
        *,
        work_id: str,
        title: str,
        source_uri: str,
        global_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]: ...

    def rewrite_for_timing(self) -> RewriteFn: ...


class VoiceAgentPort(Protocol):
    def direct_beats(self, beats_doc: Dict[str, Any]) -> Dict[str, Any]: ...

    def lock_audio(
        self,
        timeline: Dict[str, Any],
        *,
        episode_dir: Path,
        rewrite: RewriteFn,
    ) -> Dict[str, Any]: ...


class VisualAgentPort(Protocol):
    def direct_beats(self, beats_doc: Dict[str, Any]) -> Dict[str, Any]: ...

    def direct_timeline(self, timeline: Dict[str, Any]) -> Dict[str, Any]: ...

    def ensure_character_sheets(
        self, cast: List[Dict[str, Any]], *, output_root: Path
    ) -> None: ...

    def render_stills(self, timeline: Dict[str, Any], *, output_root: Path) -> None: ...

    def render_videos(
        self,
        timeline: Dict[str, Any],
        *,
        output_root: Path,
        timeline_path: Path,
        renderer: Optional[Callable[..., Any]] = None,
    ) -> Any: ...


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _audit(doc: Dict[str, Any], *, actor: str, action: str, detail: str) -> None:
    doc.setdefault("audit", []).append(
        {"at": _now(), "actor": actor, "action": action, "detail": detail}
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass
class LLMLanguageAgent:
    llm: LuoxiaLLM

    def adapt(
        self,
        text: str,
        *,
        work_id: str,
        title: str,
        source_uri: str,
        global_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        doc = analyze_novel(
            text,
            work_id=work_id,
            title=title,
            source_uri=source_uri,
            llm=self.llm,
            global_overrides=global_overrides,
            language_only=True,
        )
        _audit(
            doc,
            actor="agent:language",
            action="adapt",
            detail=(
                "story selection candidates and final dialogue text only; "
                "no visual or voice direction"
            ),
        )
        return doc

    def rewrite_for_timing(self) -> RewriteFn:
        """Return the language-owned constrained rewrite used by the audio solver."""
        return make_rewrite_fn(self.llm)


@dataclass
class LLMVoiceAgent:
    llm: LuoxiaLLM

    def direct_beats(self, beats_doc: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm.is_configured:
            raise AgentStageError("voice agent requires a configured LLM")

        preferred_ids = list(voices_for_gender(None))
        records_by_id = {
            str(item.get("id")): item
            for item in configured_voice_records()
            if item.get("id")
        }
        catalog = [
            records_by_id[voice_id]
            for voice_id in preferred_ids
            if voice_id in records_by_id
        ]
        if not catalog:
            raise AgentStageError("configured TTS provider exposes no voices")
        cast = beats_doc.get("cast") or []
        cast_request = {
            "voice_catalog": catalog,
            "cast": [
                {
                    "character_id": item.get("character_id"),
                    "display_name": item.get("display_name"),
                    "role": item.get("role"),
                    "story_context": [
                        {
                            "summary": beat.get("summary"),
                            "lines": [
                                line.get("text")
                                for line in (beat.get("lines") or [])
                                if line.get("character_id") == item.get("character_id")
                            ],
                        }
                        for beat in (beats_doc.get("beats") or [])
                        if any(
                            line.get("character_id") == item.get("character_id")
                            for line in (beat.get("lines") or [])
                        )
                    ],
                }
                for item in cast
            ],
        }
        try:
            cast_result = self.llm.chat_json(
                [
                    {"role": "system", "content": VOICE_CAST_SYSTEM},
                    {"role": "user", "content": _json(cast_request)},
                ]
            )
        except Exception as exc:
            raise AgentStageError(f"voice casting failed: {exc}") from exc

        allowed = set(preferred_ids)
        assignments = {
            str(item.get("character_id")): str(item.get("voice_id"))
            for item in (cast_result.get("cast") or [])
            if item.get("character_id") and item.get("voice_id") in allowed
        }
        missing_cast = [
            str(item.get("character_id") or "")
            for item in cast
            if str(item.get("character_id") or "") not in assignments
        ]
        if missing_cast:
            raise AgentStageError(
                "voice agent omitted a valid catalog voice for: " + ", ".join(missing_cast)
            )
        for item in cast:
            cid = str(item.get("character_id") or "")
            item["voice_id"] = assignments[cid]

        retained = [
            beat
            for beat in (beats_doc.get("beats") or [])
            if beat.get("decision") in RETAINED and beat.get("lines")
        ]
        performance_request = {
            "beats": [
                {
                    "beat_id": beat.get("beat_id"),
                    "beat_type": beat.get("beat_type"),
                    "intensity": beat.get("intensity"),
                    "summary": beat.get("summary"),
                    "lines": [
                        {
                            "line_index": index,
                            "character_id": line.get("character_id"),
                            "text": line.get("text"),
                        }
                        for index, line in enumerate(beat.get("lines") or [])
                    ],
                }
                for beat in retained
            ]
        }
        directed = 0
        directed_keys = set()
        if performance_request["beats"]:
            try:
                performance_result = self.llm.chat_json(
                    [
                        {"role": "system", "content": VOICE_PERFORMANCE_SYSTEM},
                        {"role": "user", "content": _json(performance_request)},
                    ]
                )
            except Exception as exc:
                raise AgentStageError(f"voice performance direction failed: {exc}") from exc

            by_id = {str(beat.get("beat_id")): beat for beat in retained}
            for item in performance_result.get("lines") or []:
                beat = by_id.get(str(item.get("beat_id") or ""))
                try:
                    line_index = int(item.get("line_index"))
                except (TypeError, ValueError):
                    continue
                lines = (beat or {}).get("lines") or []
                if line_index < 0 or line_index >= len(lines):
                    continue
                line = lines[line_index]
                delivery = str(item.get("delivery") or "").strip() or None
                performance = normalize_performance(
                    str(line.get("text") or ""), item.get("performance")
                )
                if not delivery or not performance:
                    continue
                line["delivery"] = delivery
                line["performance"] = performance
                directed += 1
                directed_keys.add((str(beat.get("beat_id")), line_index))

            expected_keys = {
                (str(beat.get("beat_id")), index)
                for beat in retained
                for index, _line in enumerate(beat.get("lines") or [])
            }
            missing_lines = sorted(expected_keys - directed_keys)
            if missing_lines:
                labels = [f"{beat_id}[{line_index}]" for beat_id, line_index in missing_lines]
                raise AgentStageError(
                    "voice agent omitted valid performance direction for: "
                    + ", ".join(labels)
                )

        _audit(
            beats_doc,
            actor="agent:voice",
            action="direct_beats",
            detail=f"cast={len(cast)} lines={directed}; dialogue text unchanged",
        )
        return beats_doc

    def lock_audio(
        self,
        timeline: Dict[str, Any],
        *,
        episode_dir: Path,
        rewrite: RewriteFn,
    ) -> Dict[str, Any]:
        solve_timeline(
            timeline,
            synthesize=make_tts_synthesize(episode_dir, timeline),
            rewrite=rewrite,
        )
        validate_timeline(timeline)
        _audit(
            timeline,
            actor="agent:voice",
            action="lock_audio",
            detail="measured audio durations locked before final visual direction",
        )
        return timeline


@dataclass
class LLMVisualAgent:
    llm: LuoxiaLLM

    def direct_beats(self, beats_doc: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm.is_configured:
            raise AgentStageError("visual agent requires a configured LLM")

        cast = beats_doc.get("cast") or []
        retained = [
            beat
            for beat in (beats_doc.get("beats") or [])
            if beat.get("decision") in RETAINED
        ]
        global_settings = beats_doc.get("global") or {}
        coverage = coverage_settings(beats_doc)
        context = [
            {
                "beat_id": beat.get("beat_id"),
                "beat_type": beat.get("beat_type"),
                "intensity": beat.get("intensity"),
                "summary": beat.get("summary"),
                "scene_id": beat.get("scene_id"),
                "shot_budget": coverage_budget(beat, coverage, global_settings),
                "silent_shot_budget": max(
                    0,
                    coverage_budget(beat, coverage, global_settings)
                    - len(beat.get("lines") or []),
                ),
                "lines": [
                    {
                        "line_index": index,
                        "character_id": line.get("character_id"),
                        "text": line.get("text"),
                    }
                    for index, line in enumerate(beat.get("lines") or [])
                ],
            }
            for beat in retained
        ]

        try:
            cast_result = self.llm.chat_json(
                [
                    {"role": "system", "content": VISUAL_CAST_SYSTEM},
                    {
                        "role": "user",
                        "content": _json(
                            {
                                "cast": [
                                    {
                                        "character_id": item.get("character_id"),
                                        "display_name": item.get("display_name"),
                                        "role": item.get("role"),
                                    }
                                    for item in cast
                                ],
                                "story_context": context,
                            }
                        ),
                    },
                ]
            )
            coverage_result = self.llm.chat_json(
                [
                    {"role": "system", "content": VISUAL_COVERAGE_SYSTEM},
                    {
                        "role": "user",
                        "content": _json(
                            {
                                "cast": [
                                    {
                                        "character_id": item.get("character_id"),
                                        "display_name": item.get("display_name"),
                                    }
                                    for item in cast
                                ],
                                "beats": context,
                            }
                        ),
                    },
                ]
            )
        except Exception as exc:
            raise AgentStageError(f"visual direction failed: {exc}") from exc

        appearances = {
            str(item.get("character_id")): str(item.get("appearance") or "").strip()
            for item in (cast_result.get("cast") or [])
            if item.get("character_id") and str(item.get("appearance") or "").strip()
        }
        missing_appearance: List[str] = []
        for item in cast:
            cid = str(item.get("character_id") or "")
            if item.get("role") == "narrator":
                continue
            appearance = appearances.get(cid)
            if appearance:
                item["appearance"] = appearance
            elif not (item.get("appearance") or "").strip():
                missing_appearance.append(cid)
        if missing_appearance:
            raise AgentStageError(
                "visual agent omitted reusable appearance for: " + ", ".join(missing_appearance)
            )

        cast_ids = {str(item.get("character_id")) for item in cast}
        valid_sizes = {"extreme_close_up", "close_up", "medium", "full", "wide", "insert"}
        valid_kinds = {"establishing", "reaction", "insert", "action"}
        directions = {
            str(item.get("beat_id")): item
            for item in (coverage_result.get("beats") or [])
            if item.get("beat_id")
        }
        missing_directions = [
            str(beat.get("beat_id") or "")
            for beat in retained
            if str(beat.get("beat_id") or "") not in directions
        ]
        if missing_directions:
            raise AgentStageError(
                "visual agent omitted shot direction for: "
                + ", ".join(missing_directions)
            )
        visual_count = 0
        missing_payload: List[str] = []
        for beat in retained:
            bid = str(beat.get("beat_id") or "")
            direction = directions.get(bid) or {}
            lines = beat.get("lines") or []
            sizes = direction.get("line_shot_sizes") or []
            if len(sizes) != len(lines) or any(size not in valid_sizes for size in sizes):
                raise AgentStageError(
                    f"visual agent returned invalid line_shot_sizes for {bid}: "
                    f"expected {len(lines)} valid value(s)"
                )
            for index, line in enumerate(lines):
                line["shot_size"] = sizes[index]

            visuals: List[Dict[str, Any]] = []
            for raw in direction.get("visuals") or []:
                kind = str(raw.get("kind") or "")
                if kind not in valid_kinds:
                    continue
                try:
                    after_line = int(raw.get("after_line") or 0)
                except (TypeError, ValueError):
                    after_line = 0
                after_line = max(0, min(len(lines), after_line))
                subject = str(raw.get("subject") or "").strip() or None
                if subject not in cast_ids:
                    subject = None
                if kind == "reaction" and not subject:
                    continue
                visible_characters: List[str] = []
                raw_characters = raw.get("characters")
                if isinstance(raw_characters, list):
                    for cid in raw_characters:
                        cid = str(cid or "").strip()
                        if cid in cast_ids and cid not in visible_characters:
                            visible_characters.append(cid)
                        if len(visible_characters) == 3:
                            break
                if kind == "reaction":
                    visible_characters = [subject]
                elif not visible_characters:
                    for line in lines:
                        cid = str(line.get("character_id") or "")
                        if cid in cast_ids and cid not in visible_characters:
                            visible_characters.append(cid)
                        if len(visible_characters) == 3:
                            break
                size = raw.get("shot_size")
                if size not in valid_sizes:
                    size = "close_up" if kind == "reaction" else None
                try:
                    duration = float(raw.get("action_duration_s") or 0)
                except (TypeError, ValueError):
                    duration = 0.0
                duration = max(1.0, min(15.0, duration or (1.2 if kind == "reaction" else 2.5)))
                prompt = str(raw.get("prompt") or "").strip()
                if not prompt:
                    continue
                visuals.append(
                    {
                        "kind": kind,
                        "after_line": after_line,
                        "subject": subject,
                        "characters": visible_characters,
                        "scene_id": raw.get("scene_id") or beat.get("scene_id"),
                        "shot_size": size,
                        "prompt": prompt,
                        "action_duration_s": duration,
                    }
                )
            visuals.sort(key=lambda item: item["after_line"])
            beat.pop("visual", None)
            beat["visuals"] = visuals
            visual_count += len(visuals)
            if not lines and not visuals:
                missing_payload.append(bid)

        if missing_payload:
            raise AgentStageError(
                "visual agent left retained silent beats unfilmable: " + ", ".join(missing_payload)
            )

        _audit(
            beats_doc,
            actor="agent:visual",
            action="direct_beats",
            detail=(
                f"appearances={len(appearances)} visuals={visual_count} "
                "story and dialogue unchanged"
            ),
        )
        return beats_doc

    def direct_timeline(self, timeline: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm.is_configured:
            raise AgentStageError("visual agent requires a configured LLM")

        polish_timeline_prompts(
            timeline,
            llm=self.llm,
            force_still=True,
            force_motion=True,
            strict=True,
        )
        sequence = [
            {
                "shot_id": shot.get("shot_id"),
                "index": shot.get("index"),
                "type": shot.get("type"),
                "scene_id": shot.get("scene_id"),
                "shot_size": shot.get("shot_size"),
                "target_duration_s": (shot.get("timing") or {}).get(
                    "target_duration_s"
                ),
                "dialogue": (shot.get("dialogue") or {}).get("text"),
                "description": (shot.get("subtitle") or {}).get("description"),
            }
            for shot in (timeline.get("shots") or [])
        ]
        try:
            result = self.llm.chat_json(
                [
                    {"role": "system", "content": TIMELINE_TRANSITION_SYSTEM},
                    {"role": "user", "content": _json({"shots": sequence})},
                ]
            )
        except Exception as exc:
            raise AgentStageError(f"timeline transition direction failed: {exc}") from exc

        requested = {
            str(item.get("shot_id")): item
            for item in (result.get("shots") or [])
            if item.get("shot_id")
        }
        shots = timeline.get("shots") or []
        missing_transitions = [
            str(shot.get("shot_id") or "")
            for shot in shots
            if str(shot.get("shot_id") or "") not in requested
        ]
        if missing_transitions:
            raise AgentStageError(
                "visual agent omitted transition decision for: "
                + ", ".join(missing_transitions)
            )
        non_cut = 0
        for index, shot in enumerate(shots):
            direction = requested.get(str(shot.get("shot_id"))) or {}
            kind = str(direction.get("kind") or "cut")
            if kind not in {"cut", "fade_black", "fade_white", DISSOLVE}:
                kind = "cut"
            try:
                duration = float(direction.get("duration_s") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0

            if kind != "cut" and index < len(shots) - 1:
                next_shot = shots[index + 1]
                if kind == DISSOLVE:
                    available = head_room_s(next_shot)
                else:
                    available = min(tail_room_s(shot), head_room_s(next_shot))
                duration = min(max(0.0, duration), 1.5, max(0.0, available))
                if duration > 0.0:
                    non_cut += 1
                else:
                    kind = "cut"
            else:
                kind = "cut"
                duration = 0.0

            shot["transition"] = {
                "kind": kind,
                "duration_s": duration,
                "note": str(direction.get("note") or "").strip() or None,
            }

        validate_timeline(timeline)
        _audit(
            timeline,
            actor="agent:visual",
            action="direct_timeline",
            detail=(
                "final still, motion and transition direction authored after measured "
                f"audio timing; non_cut_transitions={non_cut}"
            ),
        )
        return timeline

    def ensure_character_sheets(
        self,
        cast: List[Dict[str, Any]],
        *,
        output_root: Path,
    ) -> None:
        ensure_character_sheets(cast, output_root=output_root)

    def render_stills(self, timeline: Dict[str, Any], *, output_root: Path) -> None:
        render_timeline_stills(timeline, output_root=output_root)

    def render_videos(
        self,
        timeline: Dict[str, Any],
        *,
        output_root: Path,
        timeline_path: Path,
        renderer: Optional[Callable[..., Any]] = None,
    ) -> Any:
        render = renderer or render_timeline_videos
        return render(timeline, output_root=output_root, timeline_path=timeline_path)


@dataclass
class ProductionOrchestrator:
    language: LanguageAgentPort
    voice: VoiceAgentPort
    visual: VisualAgentPort

    @classmethod
    def default(cls, *, llm: LuoxiaLLM) -> "ProductionOrchestrator":
        return cls(
            language=LLMLanguageAgent(llm),
            voice=LLMVoiceAgent(llm),
            visual=LLMVisualAgent(llm),
        )

    def create_beats(
        self,
        text: str,
        *,
        work_id: str,
        title: str,
        source_uri: str,
        global_overrides: Optional[Dict[str, Any]] = None,
        max_repair_severity: Optional[str] = None,
    ) -> Dict[str, Any]:
        doc = self.language.adapt(
            text,
            work_id=work_id,
            title=title,
            source_uri=source_uri,
            global_overrides=global_overrides,
        )
        return self.direct_existing_beats(
            doc,
            max_repair_severity=max_repair_severity,
        )

    def direct_existing_beats(
        self,
        doc: Dict[str, Any],
        *,
        max_repair_severity: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Attach voice and visual direction to a scored document, then lock it."""
        prepared = prepare_selection(doc, plan=False, defer_payload=True)
        self.voice.direct_beats(doc)
        self.visual.direct_beats(doc)
        plan_episodes(doc)
        finalize_selection(
            doc,
            preparation=prepared,
            actor="orchestrator:production",
            max_repair_severity=max_repair_severity,
        )
        validate_beats(doc)
        _audit(
            doc,
            actor="orchestrator:production",
            action="lock_beats",
            detail="language, voice and visual directions accepted into one beats document",
        )
        return doc

    def build_timeline(
        self,
        beats_doc: Dict[str, Any],
        episode_id: str,
        *,
        provider: str,
        model: str,
    ) -> Dict[str, Any]:
        timeline = build_timeline_draft(
            beats_doc,
            episode_id,
            provider=provider,
            model=model,
        )
        _audit(
            timeline,
            actor="orchestrator:production",
            action="bridge",
            detail="one selected beats document bridged into one audio-first timeline",
        )
        return timeline

    def lock_audio(
        self,
        timeline: Dict[str, Any],
        *,
        episode_dir: Path,
    ) -> Dict[str, Any]:
        """Let voice measure timing while routing any text rewrite to language."""
        result = self.voice.lock_audio(
            timeline,
            episode_dir=episode_dir,
            rewrite=self.language.rewrite_for_timing(),
        )
        rewritten = [
            shot.get("shot_id")
            for shot in (timeline.get("shots") or [])
            if int((shot.get("dialogue") or {}).get("rewrite_count") or 0) > 0
        ]
        if rewritten:
            _audit(
                timeline,
                actor="agent:language",
                action="rewrite_for_timing",
                detail=(
                    f"constrained dialogue rewrites after measured audio: "
                    f"{', '.join(str(item) for item in rewritten)}"
                ),
            )
        return result


__all__ = [
    "AgentStageError",
    "LanguageAgentPort",
    "VoiceAgentPort",
    "VisualAgentPort",
    "LLMLanguageAgent",
    "LLMVoiceAgent",
    "LLMVisualAgent",
    "ProductionOrchestrator",
]
