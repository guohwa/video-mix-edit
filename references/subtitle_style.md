# 字幕样式与 Whisper 修正指南（video-mix-edit）

本 skill 默认使用 Whisper（base 模型，中文）做口播自动字幕，并把字幕**烧录**进成片。
烧录用的样式在 `scripts/build.py` 的 `burn_subtitles()` 里通过 `force_style` 指定，当前默认：

```
FontSize=30
PrimaryColour=&HFFFFFF   # 白字
OutlineColour=&H000000   # 黑描边
Outline=2
Shadow=1
Alignment=2             # 底部居中
MarginV=40
FontName=Microsoft YaHei
```

如需改样式（字号、颜色、位置），直接改 `build.py` 中 `style` 字符串即可。
竖屏建议把 `FontSize` 调到 36~40、`MarginV` 调到 60 左右更耐看。

## ⚠️ Whisper 字幕必须人工修正
Whisper base 对中文的识别**必然出现**：
- 繁体字（它训练语料混了大量繁体中文）
- 同音错字（"账号"↔"帐号"、"部署"↔"部属"）
- 标点错乱、吞字、断句不合理

脚本只负责生成 `.srt` 并烧录，**不修正任何语义错误**。交付前请务必：
1. 打开成片，逐句对照原声听一遍；
2. 把繁体改简体、错字改对、断句调顺；
3. 再决定是否重新烧录（可手动 `ffmpeg -i final.mp4 -vf "subtitles=fixed.srt:..." -c:a copy out.mp4`）。

## 关闭字幕
在 `plan.json` 设：
```json
"subtitle": { "enabled": false }
```
此时不调用 Whisper，成片不含字幕（也不依赖 Whisper 是否安装）。

## 字体说明
- 烧录字幕用 `FontName=Microsoft YaHei`，依赖系统字体（Windows 自带）。
- CTA 红字用 `C:/Windows/Fonts/msyh.ttc`（脚本自动复制到临时目录，避免 filtergraph 中盘符冒号问题）。
- 非 Windows / 无微软雅黑的环境：需在 `build.py` 把 `FontName` 改成环境中存在的 CJK 字体名，或把 `fontfile_rel` 指向有效字体文件。
