---
name: video-mix-edit
agent_created: true
description: "通用视频混剪 skill。用户给 2 条及以上视频素材 + 用大白话说要求（顺序、每段保留前 N 秒 / 掐头去尾、转场特效、去气口、字幕、BGM、结尾 CTA），把它剪成一条视频。支持横竖混比例自动统一、xfade 转场、Whisper 自动字幕、可选 BGM。不含促销花字。全程本地 ffmpeg + Whisper，无需外部服务。触发词：把这几段拼成一条 / 合并这几段 / 按我的要求剪这几段 / 混剪。"
---
# video-mix-edit — 通用视频混剪
把 2 条及以上视频素材，按用户的自然语言要求，混剪成一条视频。
## 工作流（Agent 必须遵循）
用户给素材 + 要求后，**不要直接跑**，按下面四步走：
1. **解析需求** —— 从用户的话里拆出：
- `segments`：每条素材的文件路径、拼接顺序
- 每条是否需要 `trim`（保留前 N 秒 → `{"end": N}`；掐头 → `{"start": N}`；取区间 → `{"start": a, "end": b}`）
- 转场：`transition`（xfade 类型）+ 是否每段不同
- `resolution`：竖屏 9:16 / 横屏 16:9 / 不指定就用 `auto`（沿用首段）
- 去气口：`strip_silence` 是否开（默认关）
- 字幕：默认开；用户说"不要字幕"才 `enabled: false`
- BGM：用户给了音频文件才加，否则不加
- CTA：用户要结尾购买引导才加
2. **写 plan.json** —— 按下方字段契约，把需求翻译成结构化计划。
3. **先确认再跑** —— 视频重编码成本高，把"剪辑计划"用简洁列表列给用户确认（特别确认比例、转场、trim 区间、输出路径）。用户说"直接剪"或确认后才执行。
4. **执行并交付** —— 运行 build.py，交付成品路径，并提醒：Whisper 字幕需逐句人工修正繁体/错字。
## 调用方式
```bash
python "C:/Users/17-0813/.workbuddy/skills/video-mix-edit/scripts/build.py" --plan plan.json
```
`plan.json` 与素材放同一目录即可（相对路径基于 plan.json 所在目录解析）。输出路径 `output` 可写绝对路径。
也支持可选参数：`--ffmpeg <path>`、`--ffprobe <path>`、`--whisper <path>`、`--keep`（保留临时目录调试）。
## plan.json 字段契约
```jsonc
{
"segments": [                 // 必填，≥2 条素材，按数组顺序拼接
{ "file": "clip1.mp4",
"trim": { "end": 3 },     // 可选：保留前 3 秒（掐头去尾用 start+end）
"strip_silence": false,   // 该段是否去静音气口（默认跟随全局）
"transition": "slideup",  // 该段与前一段的转场类型
"transition_duration": 0.5 // 转场时长（秒）；beat 模式忽略此值用 0.2
},
{ "file": "clip2.mp4" }
],
"resolution": "auto",         // auto 沿用首段；支持 9:16 / 16:9 / 1:1 / 4:3
"crop_mode": "cover",         // cover=填满裁切不留黑边(默认)；letterbox=黑边补齐
"strip_silence": false,       // 全局默认去气口（默认关，节奏紧可开）
"silence_noise_db": -35,      // 静音判定阈值
"silence_min_dur": 0.3,       // 静音最短时长（秒）
"transition": "fade",         // 全局默认转场（段级可覆盖）
"subtitle": {                 // 口播字幕；默认开
"enabled": true,
"language": "zh",
"model": "base"
},
"cta": {                      // 可选：结尾引导文案（ASS 叠加，不含促销花字）
"text": "点击下方小黄车 立即抢购",
"duration": 3.0, "color": "red", "size": 72
},
"bgm": { "file": "bgm.mp3", "volume": 0.5 },  // 可选：提供了才加
"sfx": {                            // 可选：转场音效（脚本内合成，无需素材）
  "preset_dir": "C:/path/to/your/sfx",  // 可选：放你自带的 whoosh.wav 等，同名优先用文件
  "items": [
    { "type": "whoosh", "at": "transition", "segment": 1, "volume": 0.8 },
    { "type": "riser",  "at": "transition", "segment": 1, "volume": 0.7 },
    { "type": "impact", "at": "transition", "segment": 1, "volume": 0.9 },
    { "type": "impact", "at": 3.2, "volume": 1.0 },          // at 也可写绝对秒数
    { "file": "myhit.wav", "at": 1.0, "volume": 0.8 }        // 或用自己的音频文件
  ]
},
"output": "final.mp4"
}
```
## 转场类型（transition）
写在前一段要衔接的「后一段」上（即 `segments[i].transition` 控制第 i-1→i 段）。共四类：

- **无缝硬切**：`cut` / `none` —— concat 拼接，零混合、零时长损失。做「无缝合并 / 不加转场」用这个，不要写 `xfade` 的 `duration=0`（本机 ffmpeg 9.x 会截短第二段）。
- **卡点短过渡**：`beat` —— 固定 `duration=0.2s`（忽略你写的 `transition_duration`），适合踩点快剪。
- **xfade 全家桶（约 40 种，完整列表见 `build.py` 的 `VALID_XFADE`）**，常用：
  - 溶解/遮罩类：`fade`(淡入淡出) `fadeblack`(闪黑) `fadewhite`(闪白) `fadegrayscale` `pixelize` `radial` `circlecrop` `rectcrop` `distance`
  - 滑动类：`slideleft` `slideright` `slideup` `slidedown` `smoothleft` `smoothright` `smoothup` `smoothdown`
  - 擦除类：`wipeleft` `wiperight` `wipeup` `wipedown` `hls` `hlsl` `hlsr` `vus` `vud`
  - 对角类：`diagbl` `diagbr` `diagtl` `diagtr` `hl` `hur` `hul` `hdl` `vur` `vul` `vdl` `vdr`
- **未知类型自动回退 `fade`**，不会报错。
- 全局默认转场 `fade`、默认时长 0.5s；段级 `transition` / `transition_duration` 可覆盖全局。
## 每段精确裁剪（trim）
- `{"end": 3}` —— 只保留前 3 秒（最常见：开篇钩子）
- `{"start": 2}` —— 掐掉前 2 秒
- `{"start": 1, "end": 8}` —— 只取第 1~8 秒
- 不写 `trim` 则整段使用
## 结尾 CTA（cta，可选）
- 视频末尾居中红字，出现于最后 `duration` 秒
- 默认文案"点击下方小黄车 立即抢购"，可改
- 注意：本 skill **不含促销花字叠加**（不做"限时特惠 ¥99"那类飘字）
## 转场音效（sfx，可选）
给转场点（或任意时间点）叠加短促音效，增强剪辑律动。**全部由脚本用 ffmpeg 实时合成，零外部素材依赖**；若你自带同名 `.wav`（通过 `preset_dir` 或 item 的 `file`），则优先用你的文件。

### 7 种预设（type 取值）
| 预设 | 听感 | 典型用途 |
|---|---|---|
| `whoosh` | 空气感噪声涌动（淡入淡出） | 转场"起势" |
| `riser` | 中频音 + 噪声由弱渐强的上扬感 | 转场"推进" |
| `impact` | 低频砰 + 短噪声瞬态（指数衰减） | 转场"落定"/重击 |
| `subdrop` | 超低音 45Hz 衰减 | 重低音落地，卡点用 |
| `pop` | 极短高频清脆"啵" | Vlog 轻转场 |
| `sweep` | 柔和带通噪声轻扫（音量×0.7） | Vlog 轻转场 |
| `reversecymbal` | 高通噪声反向渐强（悬念感） | 预告片式铺垫 |

### item 字段
- `type`：上述 7 种之一；或改用 `file` 指定你自己的音频（二选一）。
- `at`：摆放位置。
  - `"transition"` + `segment: N`（N≥1）：放在**第 N 段前的转场点**上。同一转场点写多个 item 时，脚本会**自动在转场时间窗内均匀错峰**（如三件套落在窗前/中/后）。
  - 数字（如 `3.2`）：放在最终时间线的绝对秒数。
- `volume`：该音效音量（0–1，默认 0.8）。
- `preset_dir`：可选目录，里面放 `whoosh.wav` 等，同名优先用文件而非合成。

### 常用组合（直接抄）
- **转场三件套**（覆盖 80% 转场）：同一 `segment` 写 `whoosh`→`riser`→`impact` 三个 item，自动错峰成"起→承→落"。
- **硬切卡点**：视频用 `transition:"cut"`，音效只放 `impact` 或 `subdrop`，对准鼓点（落在硬切边界 ±0.12s）。
- **Vlog 轻转场**：用 `pop` 或 `sweep`，别用重的，保持轻松感。
- **预告片式**：同一转场点叠加 `riser` + `reversecymbal`，悬念拉满。

⚠️ 音效叠加后音频可能整体偏响，脚本已用 `alimiter` 限幅防破音；若仍觉得吵，调低对应 `volume`。
## 依赖
- **ffmpeg / ffprobe**：必须，且需在 PATH 中（本机已安装 9.0.1-full_build）。
- **Whisper（字幕）**：默认开字幕时需要。脚本按以下优先级自动探测可用后端：
  1. `whisper` 命令行（openai-whisper，若已安装并在 PATH / venv 中）
  2. `python -m whisper`（openai-whisper 模块）
  3. `faster-whisper` 模块（**本机已预装**到隔离 venv：`C:/Users/17-0813/.workbuddy/binaries/python/envs/video-mix-edit/`，轻量、无需 torch，推荐）

  也可通过环境变量 `WHISPER_BIN`、`plan.json` 的 `whisper_bin` 字段，或命令行 `--whisper <path>` 强制指定。
  首次运行会从 HuggingFace 下载模型权重（base 约 140MB），需联网一次。
  - 脚本已自动设置 `HF_HUB_DISABLE_XET=1`，避免新版 CAS 重建服务 401 错误。
  - 若 HuggingFace 被墙/慢，可配镜像：`export HF_ENDPOINT=https://hf-mirror.com`。
  - 若模型下载始终不完整，检查代理是否允许大文件下载，或预先把模型权重放到 HuggingFace 缓存目录。

  若完全不需要字幕，可在 plan.json 设 `"subtitle": { "enabled": false }`，此时不依赖 Whisper。
## 重要提醒
⚠️ Whisper base 中文转录**必有繁体 + 错字**，口播字幕交付前必须逐句人工修正。
脚本只负责生成与烧录，不修正语义错误。详见 `references/subtitle_style.md`。
