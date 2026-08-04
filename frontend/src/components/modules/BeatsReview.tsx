"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, Film, Image as ImageIcon, Loader2,
  Play, RefreshCw, Scissors, Upload, Video,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { api, API_URL, type LuoxiaStatus, type LuoxiaBeat } from "@/lib/api";
import { useProjectStore } from "@/store/projectStore";
import { toast } from "@/store/toastStore";
import StepPageHeader, { StepPill } from "@/components/shared/StepPageHeader";

function mediaSrc(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("blob:")) {
    return url;
  }
  if (url.startsWith("/files/")) return `${API_URL}${url}`;
  if (url.startsWith("/")) return `${API_URL}${url}`;
  return `${API_URL}/files/${url.replace(/^output\//, "")}`;
}

const DECISION_STYLES: Record<string, string> = {
  keep: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  compress: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  drop: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

export default function BeatsReview() {
  const t = useTranslations("beats");
  const tc = useTranslations("common");
  const currentProject = useProjectStore((s) => s.currentProject);
  const updateProject = useProjectStore((s) => s.updateProject);

  const [status, setStatus] = useState<LuoxiaStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "keep" | "compress" | "drop">("all");
  const [previewVideo, setPreviewVideo] = useState<string | null>(null);

  const projectId = currentProject?.id;

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const s = await api.luoxiaStatus(projectId);
      setStatus(s);
    } catch (e: any) {
      console.error(e);
      toast.error(t("loadFailed"), { projectId, projectTitle: currentProject?.title });
    } finally {
      setLoading(false);
    }
  }, [projectId, currentProject?.title, t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const beats = useMemo(() => {
    const list = status?.beats ?? [];
    if (filter === "all") return list;
    return list.filter((b) => b.decision === filter);
  }, [status?.beats, filter]);

  const quality = status?.quality;
  const worst = quality?.worst_severity;

  const run = async (action: string, fn: () => Promise<any>) => {
    if (!projectId) return;
    setBusy(action);
    try {
      const result = await fn();
      const next = result?.status ?? result;
      if (next?.beats || next?.has_beats !== undefined) {
        setStatus(next);
      } else {
        await refresh();
      }
      if (result?.project) {
        updateProject(projectId, {
          ...result.project,
          originalText: result.project.original_text ?? result.project.originalText,
        });
      }
      toast.success(t("actionDone", { action: t(`action_${action}` as any) }), {
        projectId,
        projectTitle: currentProject?.title,
      });
    } catch (e: any) {
      const detail = e?.response?.data?.detail;
      if (detail?.status) setStatus(detail.status);
      const msg =
        (typeof detail === "object" && detail?.message) ||
        (typeof detail === "string" ? detail : null) ||
        e?.message ||
        t("actionFailed");
      toast.error(String(msg).slice(0, 320), {
        projectId,
        projectTitle: currentProject?.title,
      });
    } finally {
      setBusy(null);
    }
  };

  const toggleDecision = async (beat: LuoxiaBeat, decision: "keep" | "compress" | "drop") => {
    if (!projectId) return;
    await run("select", () =>
      api.luoxiaSelect(projectId, {
        decisions: [{ beat_id: beat.beat_id, decision }],
      })
    );
  };

  const onUploadRef = async (characterId: string, file: File) => {
    if (!projectId) return;
    setBusy(`ref:${characterId}`);
    try {
      const s = await api.luoxiaUploadCastReference(projectId, characterId, file);
      setStatus(s);
      toast.success(t("refUpdated"), { projectId, projectTitle: currentProject?.title });
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || t("actionFailed"), {
        projectId,
        projectTitle: currentProject?.title,
      });
    } finally {
      setBusy(null);
    }
  };

  if (!currentProject) return null;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="px-6 pt-5 pb-3 shrink-0">
        <StepPageHeader
          stepNumber={2}
          englishName="Beats"
          title={t("title")}
          subtitle={t("subtitle")}
          pills={
            <>
              {status?.beats_phase && (
                <StepPill label="beats" value={status.beats_phase} />
              )}
              {status?.timeline_phase && (
                <StepPill label="timeline" value={status.timeline_phase} />
              )}
              {worst && (
                <StepPill label="quality" value={worst} />
              )}
            </>
          }
        />

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={!!busy}
            onClick={() =>
              run("analyze", () =>
                api.luoxiaAnalyze(projectId!, {
                  text: currentProject.originalText || (currentProject as any).original_text,
                  resume: false,
                })
              )
            }
            className="glass-button px-3 py-2 text-sm flex items-center gap-2"
          >
            {busy === "analyze" ? <Loader2 size={14} className="animate-spin" /> : <Scissors size={14} />}
            {t("btnAnalyze")}
          </button>
          <button
            type="button"
            disabled={!!busy || !status?.has_beats}
            onClick={() => run("select", () => api.luoxiaSelect(projectId!))}
            className="glass-button px-3 py-2 text-sm flex items-center gap-2"
          >
            {busy === "select" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {t("btnSelect")}
          </button>
          <button
            type="button"
            disabled={!!busy || !status?.has_beats}
            onClick={() => run("bridge", () => api.luoxiaBridge(projectId!))}
            className="glass-button px-3 py-2 text-sm flex items-center gap-2"
          >
            {busy === "bridge" ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
            {t("btnBridge")}
          </button>
          <button
            type="button"
            disabled={!!busy || !status?.has_timeline}
            onClick={() => run("solve", () => api.luoxiaSolve(projectId!))}
            className="glass-button px-3 py-2 text-sm flex items-center gap-2"
          >
            {busy === "solve" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {t("btnSolve")}
          </button>
          <button
            type="button"
            disabled={!!busy || !status?.has_timeline}
            onClick={() => run("freeze", () => api.luoxiaFreeze(projectId!))}
            className="glass-button px-3 py-2 text-sm flex items-center gap-2"
          >
            {busy === "freeze" ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {t("btnFreeze")}
          </button>
          <button
            type="button"
            disabled={!!busy}
            onClick={refresh}
            className="glass-button px-3 py-2 text-sm flex items-center gap-2 ml-auto"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            {tc("sync")}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-0 min-h-0">
        {/* Beats list */}
        <div className="overflow-y-auto px-6 pb-8 space-y-3 min-h-0">
          {quality && (
            <div className="glass-panel p-4 rounded-xl border border-border">
              <div className="flex items-start gap-3">
                <AlertTriangle
                  size={18}
                  className={
                    worst === "high"
                      ? "text-rose-400"
                      : worst === "medium"
                        ? "text-amber-400"
                        : "text-text-muted"
                  }
                />
                <div className="text-sm space-y-1">
                  <p className="font-medium text-foreground">{t("qualityTitle")}</p>
                  <p className="text-text-secondary">
                    {t("qualityBody", {
                      count: quality.repair_count ?? 0,
                      worst: worst || "none",
                      invented: quality.invented_lines ?? 0,
                      truncated: quality.truncated_lines ?? 0,
                    })}
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="flex gap-2 sticky top-0 bg-background/80 backdrop-blur py-2 z-10">
            {(["all", "keep", "compress", "drop"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                  filter === f
                    ? "border-primary bg-primary/15 text-primary"
                    : "border-border text-text-secondary hover:bg-hover-bg"
                }`}
              >
                {t(`filter_${f}`)}
              </button>
            ))}
            <span className="text-xs text-text-muted self-center ml-auto">
              {t("beatCount", { n: beats.length })}
            </span>
          </div>

          {loading && !status ? (
            <div className="flex items-center justify-center py-20 text-text-secondary gap-2">
              <Loader2 className="animate-spin" size={18} />
              {tc("loading")}
            </div>
          ) : !status?.has_beats ? (
            <div className="glass-panel rounded-xl p-10 text-center text-text-secondary">
              <Scissors className="mx-auto mb-3 opacity-50" size={28} />
              <p className="font-medium text-foreground mb-1">{t("emptyTitle")}</p>
              <p className="text-sm">{t("emptyBody")}</p>
            </div>
          ) : (
            beats.map((beat) => (
              <article
                key={beat.beat_id}
                className="glass-panel rounded-xl p-4 border border-border/80 space-y-2"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-text-muted">{beat.beat_id}</span>
                  <span className="text-xs text-text-secondary">{beat.beat_type}</span>
                  <span className="text-xs text-text-muted">
                    {t("intensity", { n: beat.intensity ?? "—" })}
                  </span>
                  <span
                    className={`text-[0.65rem] uppercase tracking-wide px-2 py-0.5 rounded border ${
                      DECISION_STYLES[beat.decision || "drop"] || DECISION_STYLES.drop
                    }`}
                  >
                    {beat.decision}
                  </span>
                  <div className="ml-auto flex gap-1">
                    {(["keep", "compress", "drop"] as const).map((d) => (
                      <button
                        key={d}
                        type="button"
                        disabled={!!busy}
                        onClick={() => toggleDecision(beat, d)}
                        className={`text-[0.65rem] px-2 py-0.5 rounded border ${
                          beat.decision === d
                            ? DECISION_STYLES[d]
                            : "border-border text-text-muted hover:bg-hover-bg"
                        }`}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                </div>
                <p className="text-sm text-foreground leading-relaxed">{beat.summary}</p>
                {beat.source_span?.excerpt && (
                  <p className="text-xs text-text-muted border-l-2 border-border pl-2 line-clamp-2">
                    {beat.source_span.excerpt}
                  </p>
                )}
                {(beat.lines || []).length > 0 && (
                  <ul className="space-y-1 mt-1">
                    {beat.lines!.map((line, i) => (
                      <li key={i} className="text-sm text-text-secondary">
                        <span className="text-primary/80 font-medium">
                          {line.character_id}
                        </span>
                        ：{line.text}
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))
          )}

          {/* Shot media strip */}
          {(status?.shots?.length ?? 0) > 0 && (
            <section className="pt-4 space-y-3">
              <h3 className="text-sm font-medium text-foreground flex items-center gap-2">
                <Video size={16} />
                {t("shotsTitle")}
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                {status!.shots!.map((shot) => (
                  <div
                    key={shot.shot_id}
                    className="glass-panel rounded-lg overflow-hidden border border-border"
                  >
                    <div className="aspect-[9/16] bg-black/40 relative flex items-center justify-center">
                      {shot.video_url ? (
                        <video
                          src={mediaSrc(shot.video_url)}
                          className="w-full h-full object-cover"
                          controls
                          playsInline
                        />
                      ) : shot.still_url ? (
                        <img
                          src={mediaSrc(shot.still_url)}
                          alt={shot.shot_id}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <ImageIcon className="text-text-muted opacity-40" size={24} />
                      )}
                    </div>
                    <div className="p-2 text-xs space-y-0.5">
                      <div className="font-mono text-text-muted">{shot.shot_id}</div>
                      <div className="text-text-secondary truncate">
                        {shot.dialogue?.text || shot.type}
                      </div>
                      {shot.request_duration_s != null && (
                        <div className="text-text-muted">
                          {t("duration", { n: shot.request_duration_s })}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {status?.final_video_url && (
            <section className="pt-2 pb-6">
              <h3 className="text-sm font-medium text-foreground mb-2 flex items-center gap-2">
                <Play size={16} />
                {t("finalTitle")}
              </h3>
              <video
                src={mediaSrc(status.final_video_url)}
                controls
                playsInline
                className="w-full max-w-sm mx-auto rounded-xl border border-border bg-black aspect-[9/16]"
              />
            </section>
          )}
        </div>

        {/* Cast rail */}
        <aside className="border-l border-border overflow-y-auto px-4 py-4 space-y-3 bg-surface/30">
          <h3 className="text-xs font-mono uppercase tracking-wider text-text-muted">
            {t("castTitle")}
          </h3>
          {(status?.cast || []).length === 0 ? (
            <p className="text-xs text-text-muted">{t("castEmpty")}</p>
          ) : (
            status!.cast!.map((c: any) => (
              <div key={c.character_id} className="glass-panel rounded-lg p-3 space-y-2">
                <div className="flex gap-2">
                  <div className="w-14 h-14 rounded-md bg-black/30 overflow-hidden shrink-0 flex items-center justify-center">
                    {c.reference_image_url ? (
                      <img
                        src={mediaSrc(c.reference_image_url)}
                        alt={c.display_name}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <ImageIcon size={16} className="text-text-muted" />
                    )}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-foreground truncate">
                      {c.display_name || c.character_id}
                    </div>
                    <p className="text-[0.7rem] text-text-muted line-clamp-3">
                      {c.appearance}
                    </p>
                  </div>
                </div>
                <label className="glass-button w-full justify-center text-xs py-1.5 flex items-center gap-1.5 cursor-pointer">
                  {busy === `ref:${c.character_id}` ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Upload size={12} />
                  )}
                  {t("uploadRef")}
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) onUploadRef(c.character_id, f);
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
            ))
          )}

          {status?.cost && (
            <div className="text-xs text-text-muted border-t border-border pt-3 space-y-1">
              <div>{t("budget", { n: status.cost.budget_ceiling_usd ?? "—" })}</div>
              {status.cost.estimated_usd != null && (
                <div>{t("estimated", { n: status.cost.estimated_usd })}</div>
              )}
            </div>
          )}
        </aside>
      </div>

      {previewVideo && (
        <div
          className="fixed inset-0 z-50 bg-overlay flex items-center justify-center"
          onClick={() => setPreviewVideo(null)}
        >
          <video
            src={previewVideo}
            controls
            autoPlay
            className="max-h-[90vh] max-w-[90vw] rounded-xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
