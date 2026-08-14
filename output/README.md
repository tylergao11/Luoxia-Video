# Output contract

`output/` 保存生产生成物、运行状态与已确认的角色/服装母资产，并作为公司与家庭工作站之间同步的生产真相。该目录及其子目录必须始终对 Git 可见；顶层不允许直接散落项目文件、脚本、回执或媒体。

```text
output/
├─ projects/<project_id>/       # beats、角色定妆与项目级真相
├─ episodes/<episode_id>/       # timeline、音频、分镜、视频、合成工作区、final.mp4
├─ studio/
│  ├─ state/                    # projects.json、series.json、library_assets.json
│  ├─ media/                    # 用户确认的母资产，以及 storyboard、audio、video、uploads、exports
│  ├─ playground/               # 独立模型试验场产物
│  ├─ cache/                    # 可重建缓存
│  └─ work/                     # 可重建临时工作文件
├─ samples/<sample_id>/
│  ├─ final/                    # 可直接查看或交付的成片
│  ├─ assets/                   # 样片输入图和中间画面
│  ├─ metadata/                 # 请求回执与实测结果
│  └─ recipes/                  # 可复现脚本
└─ runtime/                     # 本地模型、虚拟环境和权重；不是业务配置，但仍对 Git 可见
```

路径由 `src/output_contract.py` 唯一定义。生产代码不得自行拼接另一套 `output/...` 默认路径；不合法的项目 ID、越界路径或缺失文件直接报错，不读取旧布局。
