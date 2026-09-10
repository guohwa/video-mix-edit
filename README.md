# video-mix-edit

通用视频混剪 skill。给 2 条及以上视频素材，用大白话说要求（顺序、每段保留前 N 秒 / 掐头去尾、转场特效、去气口、字幕、BGM、结尾 CTA），把它剪成一条视频。

全程本地 **ffmpeg + Whisper**，无需任何外部服务。不含促销花字。

## 功能

- 横竖混比例自动统一（cover 裁切 / letterbox 黑边）
- 每段精确裁剪（保留前 N 秒 / 掐头 / 取区间）
- 转场：无缝硬切 `cut`、卡点 `beat`、xfade 约 40 种（fade / slide / circlecrop / wipe / pixelize ...），支持每段不同
- 可选去气口（`silenceremove`）
- Whisper 自动口播字幕（中文，需逐句人工修正繁体/错字）
- 可选 BGM 混音
- 可选转场音效（Whoosh / Riser / Impact / Sub Drop / Pop / Sweep / Reverse Cymbal，脚本内用 ffmpeg 实时合成，零素材依赖）
- 可选结尾 CTA（居中红字，不含促销花字）

## 安装（作为 WorkBuddy skill）

将本目录放到 WorkBuddy 的 skills 目录（用户级 `~/.workbuddy/skills/` 或项目级 `.workbuddy/skills/`），重启会话即可被 Agent 调用。

依赖：

- **ffmpeg / ffprobe**：必须，需在 PATH 中。
- **Whisper（字幕）**：默认开字幕时需要。脚本按优先级自动探测：`whisper` CLI → `python -m whisper` → `faster-whisper`（轻量、无需 torch，推荐）。首次运行会从 HuggingFace 下载模型权重（base 约 140MB），需联网一次。

## 用法
(一)直接口述告诉你安装的智能体：
 把视频A的3-5s剪切到视频B的前面，加上xx转场音效，加上xx转场特效，加上xx背景音乐。

 当然，转场特效、转场音效、背景音乐这些你可以不用特殊说明，它会自动保留原声。

 默认就有一个转场。
 
（二）用代码的方式运行：
```bash
python "C:/Users/17-0813/.workbuddy/skills/video-mix-edit/scripts/build.py" --plan plan.json
```

`plan.json` 与素材放同一目录（相对路径基于 plan.json 所在目录解析）。可选参数：`--ffmpeg <path>`、`--ffprobe <path>`、`--whisper <path>`、`--keep`（保留临时目录调试）。

## plan.json 字段契约

```jsonc
{
  "segments": [                 // 必填，≥2 条素材，按数组顺序拼接
    { "file": "clip1.mp4",
      "trim": { "end": 3 },     // 可选：保留前 3 秒
      "transition": "slideup",  // 该段与前一段的转场类型
      "transition_duration": 0.5 },
    { "file": "clip2.mp4" }
  ],
  "resolution": "auto",         // auto 沿用首段；支持 9:16 / 16:9 / 1:1 / 4:3
  "crop_mode": "cover",         // cover=填满裁切不留黑边(默认)；letterbox=黑边补齐
  "transition": "fade",         // 全局默认转场（段级可覆盖）
  "subtitle": { "enabled": true, "language": "zh", "model": "base" },
  "bgm": { "file": "bgm.mp3", "volume": 0.5 },   // 可选：提供了才加
  "sfx": {                                   // 可选：转场音效
    "items": [
      { "type": "whoosh", "at": "transition", "segment": 1, "volume": 0.8 },
      { "type": "riser",  "at": "transition", "segment": 1, "volume": 0.7 },
      { "type": "impact", "at": "transition", "segment": 1, "volume": 0.9 }
    ]
  },
  "output": "final.mp4"
}
```

- `trim`：`{"end": N}` 保留前 N 秒；`{"start": N}` 掐头；`{"start": a, "end": b}` 取区间。
- `transition`：写在「后一段」上控制第 i-1→i 段。`cut`/`none` 为真·无缝硬切；`beat` 固定 0.2s 卡点；其余走 xfade。
- `sfx.items`：`type` 为 7 种预设之一（或改用 `file` 指定自己的音频）；`at:"transition"`+`segment:N` 落在第 N 段前的转场点（同点多 item 自动均匀错峰），也可写绝对秒数。

更多示例见 `examples/plan.example.json` 与 `examples/plan.sfx.json`。

## 转场音效常用组合

- **转场三件套**（覆盖 80% 转场）：同一 `segment` 写 `whoosh`→`riser`→`impact`，自动错峰成“起→承→落”。
- **硬切卡点**：视频用 `transition:"cut"`，音效只放 `impact` 或 `subdrop`，对准硬切边界。
- **Vlog 轻转场**：用 `pop` 或 `sweep`。
- **预告片式**：同一点叠加 `riser` + `reversecymbal`。

## 重要提醒

Whisper base 中文转录**必有繁体 + 错字**，口播字幕交付前必须逐句人工修正。详见 `references/subtitle_style.md`。
