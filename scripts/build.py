#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video-mix-edit :: build.py
把 2 条及以上视频素材，按 plan.json 的设定混剪成一条视频。
全程本地：ffmpeg + (可选) Whisper，无需任何外部服务。

功能：
  - 横竖混比例自动统一（cover 裁切 / letterbox 黑边）
  - 每段精确裁剪（保留前 N 秒 / 掐头 / 取区间）
  - xfade 转场（fade / beat / slide / circlecrop / wipe ...），支持每段不同
  - 可选去气口（silenceremove）
  - Whisper 自动口播字幕（zh / base，默认开）
  - 可选 BGM 混音
  - 可选转场音效（Whoosh / Riser / Impact / Sub Drop / Pop / Sweep / Reverse Cymbal，脚本内合成，可叠加在转场点或任意时间点）
  - 可选结尾 CTA（居中红字，不含促销花字）

用法：
  python build.py --plan plan.json [--ffmpeg PATH] [--ffprobe PATH] [--whisper PATH] [--keep]
plan.json 与素材放同一目录；相对路径基于 plan.json 所在目录解析。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# ----------------------------------------------------------------- helpers
def log(msg):
    print(f"[video-mix-edit] {msg}", flush=True)

def resolve(p, base):
    if os.path.isabs(p):
        return p
    return os.path.join(base, p)

def ffprobe_json(ffprobe, path):
    r = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 读取失败: {path}\n{r.stderr}")
    return json.loads(r.stdout)

def duration_of(ffprobe, path):
    d = ffprobe_json(ffprobe, path).get("format", {}).get("duration")
    return float(d) if d else 0.0

def video_size(ffprobe, path):
    for s in ffprobe_json(ffprobe, path).get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    return 1920, 1080

def has_audio(ffprobe, path):
    for s in ffprobe_json(ffprobe, path).get("streams", []):
        if s.get("codec_type") == "audio":
            return True
    return False

def find_cjk_font():
    cands = [
        r"C:/Windows/Fonts/msyh.ttc",
        r"C:/Windows/Fonts/simhei.ttf",
        r"C:/Windows/Fonts/simsun.ttc",
        r"C:/Windows/Fonts/NotoSansSC-VF.ttf",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None

VENV_PY = r"C:/Users/17-0813/.workbuddy/binaries/python/envs/video-mix-edit/Scripts/python.exe"


def _probe(py, mod):
    if not (py and os.path.exists(py)):
        return False
    t = subprocess.run([py, "-c", f"import {mod}"],
                       capture_output=True, text=True)
    return t.returncode == 0


def find_whisper(plan, env):
    """返回 (bin_path, mode)。mode ∈ {'cli','module','fast',None}

    - cli    : openai-whisper 的 whisper 命令行
    - module : openai-whisper 的 python 模块 (python -m whisper)
    - fast   : faster-whisper python 模块（更轻量，推荐）
    """
    # 1) openai-whisper CLI
    cli_cands = [
        plan.get("whisper_bin"),
        env.get("WHISPER_BIN"),
        shutil.which("whisper"),
        os.path.join(os.path.dirname(VENV_PY), "whisper.exe"),
    ]
    for c in cli_cands:
        if c and os.path.exists(c):
            return c, "cli"
    # 2) openai-whisper python 模块
    for py in (VENV_PY, sys.executable):
        if _probe(py, "whisper"):
            return py, "module"
    # 3) faster-whisper python 模块（轻量、无需 torch）
    for py in (VENV_PY, sys.executable):
        if _probe(py, "faster_whisper"):
            return py, "fast"
    return None, None

def esc_drawtext(s):
    s = s.replace("\\", "\\\\").replace("'", "\u2019")
    s = s.replace("%", "%%").replace("$", "$$").replace("\n", " ").replace("\r", " ")
    return s

VALID_XFADE = {
    "fade", "fadeblack", "fadewhite", "circlecrop", "rectcrop",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "distance", "fadegrayscale", "pixelize", "radial",
    "hls", "hlsl", "hlsr", "vus", "vud", "diagbl", "diagbr",
    "diagtl", "diagtr", "hl", "hur", "hul", "hdl", "vur",
    "vul", "vdl", "vdr",
}
DEFAULT_TD = 0.5

# --------------------------------------------------------- per-seg normalize
def trimmed_dur(src_dur, t):
    """根据 trim 参数计算裁剪后的实际时长（秒）。"""
    if not t:
        return src_dur
    if "start" in t and "end" in t:
        return max(0.0, min(t["end"], src_dur) - t["start"])
    if "start" in t:
        return max(0.0, src_dur - t["start"])
    if "end" in t:
        return min(t["end"], src_dur)
    return src_dur


def process_segment(ffmpeg, ffprobe, src, out, tw, th, crop_mode,
                    do_silence, db, smin, trim=None):
    if crop_mode == "cover":
        vf = f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}"
    else:
        vf = (f"scale={tw}:{th}:force_original_aspect_ratio=decrease,"
              f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black")
    vf += ",fps=30,format=yuv420p,setsar=1"

    af = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    if do_silence:
        af = (f"silenceremove=start_periods=-1:start_threshold={db}dB:"
              f"start_silence={smin}:stop_periods=-1:stop_threshold={db}dB:"
              f"stop_silence={smin}:detection=peak," + af)

    cmd = [ffmpeg, "-y"]
    # 裁剪（输入级 seek，快速）
    t = trim or {}
    if "start" in t and "end" in t:
        cmd += ["-ss", str(t["start"]), "-to", str(t["end"])]
    elif "start" in t:
        cmd += ["-ss", str(t["start"])]
    elif "end" in t:
        cmd += ["-t", str(t["end"])]
    cmd += ["-i", src]

    if has_audio(ffprobe, src):
        cmd += ["-vf", vf, "-af", af,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-ar", "44100", "-y", out]
    else:
        # 无音轨素材：生成静音音轨，时长必须等于「裁剪后」时长，
        # 否则 muxer 会取较长音轨，导致 trim 看似失效。
        src_dur = duration_of(ffprobe, src)
        seg_dur = trimmed_dur(src_dur, t)
        fc = f"[0:v]{vf}[v];aevalsrc=0:channel_layout=stereo:" \
             f"sample_rate=44100:d={seg_dur:.3f}[a]"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-y", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"片段处理失败 {src}:\n{r.stderr[-1500:]}")


# ------------------------------------------------------------- concat + fx
def build_concat(ffmpeg, seg_files, seg_durs, plan, work, tmp_out,
                 fontfile_rel, cta, bgm_file):
    n = len(seg_files)
    global_trans = plan.get("transition", "fade")
    tds = [0.0] * n
    trans = [None] * n
    for i in range(1, n):
        t = plan["segments"][i].get("transition", global_trans)
        if t in ("cut", "none"):
            # 无缝硬切：零混合、零截断（xfade duration=0 在本机 ffmpeg 会截短，故用 concat）
            trans[i] = "cut"
            tds[i] = 0.0
            continue
        if t == "beat":
            td = 0.2
        else:
            td = float(plan["segments"][i].get("transition_duration", DEFAULT_TD))
        if t not in VALID_XFADE:
            t = "fade"
        trans[i] = t
        tds[i] = td

    # xfade 偏移：offset_i = C_{i-1} - sum(td[1..i])
    C = []
    s = 0.0
    for d in seg_durs:
        s += d
        C.append(s)
    tdsum = 0.0
    offsets = [0.0] * n
    for i in range(1, n):
        tdsum += tds[i]
        offsets[i] = C[i - 1] - tdsum

    total_dur = C[-1] - sum(tds[1:])

    # 转场时间窗（用于音效对齐）。trans_win[k] 对应 segments[k+1] 的衔接点。
    trans_win = []
    for i in range(1, n):
        if trans[i] == "cut":
            # 硬切边界 = 前 i 段累计（cut 无重叠），音效落在边界 ±0.12s 内
            boundary = C[i - 1]
            trans_win.append((max(0.0, boundary - 0.12), boundary + 0.12))
        else:
            trans_win.append((offsets[i], offsets[i] + tds[i]))

    parts = []
    last_v = "0:v"
    last_a = "0:a"
    for i in range(1, n):
        vn = f"vf{i}"
        an = f"af{i}"
        if trans[i] == "cut":
            # 真·硬切：concat 拼接，无转场、无时长损失
            parts.append(f"[{i-1}:v][{i}:v]concat=n=2:v=1:a=0[{vn}]")
            parts.append(f"[{i-1}:a][{i}:a]concat=n=2:v=0:a=1[{an}]")
        else:
            ti = "fade" if trans[i] == "beat" else trans[i]
            td = tds[i]
            o = offsets[i]
            parts.append(
                f"[{i-1}:v][{i}:v]xfade=transition={ti}:"
                f"duration={td:.3f}:offset={o:.3f}[{vn}]")
            parts.append(
                f"[{i-1}:a][{i}:a]acrossfade=d={td:.3f}:c1=tri:c2=tri[{an}]")
        last_v, last_a = vn, an

    # 结尾 CTA
    if cta:
        ctext = esc_drawtext(cta.get("text", "点击下方小黄车 立即抢购"))
        csize = int(cta.get("size", 72))
        cdur = float(cta.get("duration", 3.0))
        cstart = max(0.0, total_dur - cdur)
        fnt = f"fontfile={fontfile_rel}" if fontfile_rel else "font=Microsoft YaHei"
        parts.append(
            f"[{last_v}]drawtext={fnt}:text='{ctext}':"
            f"fontcolor={cta.get('color','red')}:fontsize={csize}:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:"
            f"enable='between(t,{cstart:.2f},{total_dur:.2f})'[vcta]")
        last_v = "vcta"

    audio_out = last_a
    if bgm_file:
        bi = n  # bgm 是下一个输入
        parts.append(
            f"[{bi}:a]volume={float(plan['bgm'].get('volume',0.5))},"
            f"aloop=loop=-1[bg]")
        parts.append(
            f"[{last_a}][bg]amix=inputs=2:duration=first:"
            f"dropout_transition=0[aout]")
        audio_out = "aout"

    fc = ";".join(parts)

    cmd = [ffmpeg, "-y"]
    for sfile in seg_files:
        cmd += ["-i", sfile]
    if bgm_file:
        cmd += ["-i", bgm_file]
    cmd += ["-filter_complex", fc,
            "-map", f"[{last_v}]", "-map", f"[{audio_out}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", tmp_out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if r.returncode != 0:
        raise RuntimeError(f"混流失败:\n{r.stderr[-2000:]}")
    return total_dur, trans_win


FASTER_WHISPER_HELPER = """
import sys
from faster_whisper import WhisperModel

def fmt(t):
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)

audio, model, lang, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
m = WhisperModel(model, device="cpu", compute_type="int8")
segs, _ = m.transcribe(audio, language=(lang or None), beam_size=5)
with open(out, "w", encoding="utf-8") as f:
    idx = 1
    for s in segs:
        f.write("%d\\n%s --> %s\\n%s\\n\\n" % (idx, fmt(s.start), fmt(s.end), s.text.strip()))
        idx += 1
"""


def run_whisper(whisper_bin, mode, audio_wav, model, language, work):
    if mode == "cli":
        cmd = [whisper_bin, audio_wav, "--model", model,
               "--output_format", "srt", "--output_dir", work]
        if language:
            cmd += ["--language", language]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
        base = os.path.splitext(os.path.basename(audio_wav))[0]
        out = os.path.join(work, base + ".srt")
    elif mode == "module":
        cmd = [whisper_bin, "-m", "whisper", audio_wav, "--model", model,
               "--output_format", "srt", "--output_dir", work]
        if language:
            cmd += ["--language", language]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
        base = os.path.splitext(os.path.basename(audio_wav))[0]
        out = os.path.join(work, base + ".srt")
    elif mode == "fast":
        helper = os.path.join(work, "_fw.py")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(FASTER_WHISPER_HELPER)
        out = os.path.join(work, "tmp.srt")
        cmd = [whisper_bin, helper, audio_wav, model, language or "", out]
        # 禁用 Xet（HF 新版 CAS 重建服务），避免 401/不完整下载
        fw_env = os.environ.copy()
        fw_env.setdefault("HF_HUB_DISABLE_XET", "1")
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=work, env=fw_env)
    else:
        raise RuntimeError("未知的 Whisper 模式")
    if r.returncode != 0:
        raise RuntimeError(f"Whisper 失败:\n{r.stderr[-1500:]}")
    if not os.path.exists(out):
        raise RuntimeError(f"Whisper 未生成字幕文件: {out}\n{r.stderr[-1500:]}")
    return out


def burn_subtitles(ffmpeg, work, src_mp4, srt_name, style, out):
    vf = f"subtitles={srt_name}:fontsdir=.:force_style='{style}'"
    r = subprocess.run(
        [ffmpeg, "-y", "-i", src_mp4, "-vf", vf,
         "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", out],
        capture_output=True, text=True, cwd=work)
    if r.returncode != 0:
        raise RuntimeError(f"字幕烧录失败:\n{r.stderr[-1500:]}")


# ------------------------------------------------------------------- SFX
# 转场音效预设（脚本内用 ffmpeg 合成，无需任何外部音频素材）
# 每种预设 = 若干 lavfi 音源 + 一段 filtergraph，输出带 [o] 标签。
SFX_RECIPES = {
    "whoosh": dict(
        src=["anoisesrc=d=0.45:c=pink:r=44100"],
        fc="[0]highpass=f=250,lowpass=f=5000,"
           "volume='if(lt(t,0.08),t/0.08,if(gt(t,0.37),max(0,(0.45-t)/0.08),1))':"
           "eval=frame[o]"),
    "riser": dict(
        src=["sine=frequency=300:d=0.5",
             "anoisesrc=d=0.5:c=white:r=44100,highpass=f=1500"],
        fc="[0]volume='t/0.5':eval=frame[a];"
           "[1]volume='0.6*(t/0.5)':eval=frame[b];"
           "[a][b]amix=inputs=2:normalize=0[o]"),
    "impact": dict(
        src=["sine=frequency=55:d=0.4,volume='exp(-9*t)':eval=frame",
             "anoisesrc=d=0.05:c=white:r=44100,volume=0.7"],
        fc="[0][1]amix=inputs=2:normalize=0[o]"),
    "subdrop": dict(
        src=["sine=frequency=45:d=0.45,lowpass=f=160"],
        fc="[0]volume='exp(-6*t)':eval=frame[o]"),
    "pop": dict(
        src=["sine=frequency=880:d=0.1"],
        fc="[0]highpass=f=400,volume='exp(-35*t)':eval=frame[o]"),
    "sweep": dict(
        src=["anoisesrc=d=0.35:c=pink:r=44100"],
        fc="[0]bandpass=f=1500:width_type=h:w=900,"
           "volume='if(lt(t,0.06),t/0.06,if(gt(t,0.29),max(0,(0.35-t)/0.06),1))*0.7':"
           "eval=frame[o]"),
    "reversecymbal": dict(
        src=["anoisesrc=d=0.6:c=white:r=44100,highpass=f=3500"],
        fc="[0]volume='pow(min(t/0.6,1),1.6)':eval=frame[o]"),
}
SFX_NAMES = list(SFX_RECIPES.keys())


def synth_sfx(ffmpeg, work, name, preset_dir=None):
    """合成（或取用户自带）指定预设音效，返回 WAV 路径。"""
    if name not in SFX_RECIPES:
        raise ValueError(f"未知音效预设: {name}（可选: {', '.join(SFX_NAMES)}）")
    # 用户自带同名 wav 优先
    if preset_dir:
        cand = os.path.join(preset_dir, f"{name}.wav")
        if os.path.exists(cand):
            return cand
    out = os.path.join(work, f"__sfx_{name}.wav")
    if os.path.exists(out):
        return out
    spec = SFX_RECIPES[name]
    cmd = [ffmpeg, "-y"]
    for s in spec["src"]:
        cmd += ["-f", "lavfi", "-i", s]
    cmd += ["-filter_complex", spec["fc"], "-map", "[o]",
            "-c:a", "pcm_s16le", out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if r.returncode != 0:
        raise RuntimeError(f"音效合成失败 {name}:\n{r.stderr[-1200:]}")
    return out


def _sfx_placements(plan, trans_win, total_dur, work, ffmpeg, base):
    """把 plan.sfx.items 翻译成 [(wav_path, start_sec, volume), ...]。"""
    sfx = plan.get("sfx") or {}
    items = sfx.get("items") or []
    if not items:
        return []
    preset_dir = sfx.get("preset_dir")
    if preset_dir:
        preset_dir = resolve(preset_dir, base)

    placements = []
    # 先把 at:transition 的项按 (segment) 分组，做窗口内均匀分布
    trans_groups = {}
    for it in items:
        if it.get("at") == "transition":
            seg = it.get("segment")
            if seg is None or seg < 1:
                raise ValueError("at:transition 必须指定 segment（≥1，表示第几段前的转场）")
            trans_groups.setdefault(seg, []).append(it)

    # 处理 transition 项（带窗口均匀错峰）
    for seg, group in trans_groups.items():
        if seg - 1 >= len(trans_win):
            raise ValueError(f"segment={seg} 超出转场数量 {len(trans_win)}")
        ws, we = trans_win[seg - 1]
        n = len(group)
        for j, it in enumerate(group):
            start = ws + (j + 0.5) / n * max(0.001, (we - ws))
            path = _one_sfx_path(it, work, ffmpeg, preset_dir, base)
            vol = float(it.get("volume", 0.8))
            placements.append((path, start, vol))

    # 处理绝对时间项（at: 数字）与自定义 file
    for it in items:
        if it.get("at") == "transition":
            continue
        start = float(it["at"]) if "at" in it else 0.0
        path = _one_sfx_path(it, work, ffmpeg, preset_dir, base)
        vol = float(it.get("volume", 0.8))
        placements.append((path, start, vol))
    return placements


def _one_sfx_path(it, work, ffmpeg, preset_dir, base):
    if it.get("file"):
        p = resolve(it["file"], base)
        if not os.path.exists(p):
            raise FileNotFoundError(f"音效文件不存在: {p}")
        return p
    if it.get("type"):
        return synth_sfx(ffmpeg, work, it["type"], preset_dir)
    raise ValueError(f"音效项必须提供 type 或 file: {it}")


def apply_sfx(ffmpeg, src_mp4, placements, total_dur, out, work):
    """把若干音效按时间点叠加到 src_mp4 的音轨上（视频流直接 copy）。"""
    if not placements:
        shutil.copy(src_mp4, out)
        return
    cmd = [ffmpeg, "-y", "-i", src_mp4]
    for path, _, _ in placements:
        cmd += ["-i", path]
    parts = []
    for idx, (_, start, vol) in enumerate(placements, start=1):
        delay = int(round(start * 1000))
        parts.append(
            f"[{idx}:a]adelay=delays={delay}:all=1,"
            f"apad=whole_dur={total_dur:.3f},"
            f"atrim=0:{total_dur:.3f},"
            f"volume={vol:.3f}[s{idx}]")
    amix_in = "[0:a]" + "".join(f"[s{idx}]" for idx in range(1, len(placements) + 1))
    parts.append(
        f"{amix_in}amix=inputs={len(placements) + 1}:normalize=0,"
        f"alimiter=level_in=1:level_out=1:limit=0.98:asc=1:"
        f"attack=5:release=50[aout]")
    fc = ";".join(parts)
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-ar", "44100", out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if r.returncode != 0:
        raise RuntimeError(f"音效叠加失败:\n{r.stderr[-2000:]}")


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--ffmpeg")
    ap.add_argument("--ffprobe")
    ap.add_argument("--whisper")
    ap.add_argument("--keep", action="store_true",
                    help="保留临时目录用于调试")
    args = ap.parse_args()

    plan_path = os.path.abspath(args.plan)
    base = os.path.dirname(plan_path)
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    ffmpeg = args.ffmpeg or plan.get("ffmpeg") or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or plan.get("ffprobe") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        sys.exit("ERROR: 未找到 ffmpeg / ffprobe，请先安装并加入 PATH。")
    log(f"ffmpeg: {ffmpeg}")
    log(f"ffprobe: {ffprobe}")

    segs = plan.get("segments")
    if not segs or len(segs) < 1:
        sys.exit("ERROR: plan.json 至少需要 1 条 segment。")
    log(f"素材数量: {len(segs)}")

    # 目标分辨率
    RES = {"9:16": (1080, 1920), "16:9": (1920, 1080),
           "1:1": (1080, 1080), "4:3": (1440, 1080)}
    res = plan.get("resolution", "auto")
    crop_mode = plan.get("crop_mode", "cover")
    if res == "auto":
        first = resolve(segs[0]["file"], base)
        tw, th = video_size(ffprobe, first)
        log(f"resolution=auto → 沿用首段 {tw}x{th}")
    else:
        tw, th = RES.get(res, (1920, 1080))
        log(f"resolution={res} → 目标 {tw}x{th}")

    do_silence_global = plan.get("strip_silence", False)
    db = plan.get("silence_noise_db", -35)
    smin = plan.get("silence_min_dur", 0.3)

    work = tempfile.mkdtemp(prefix="videomix_")
    try:
        # 1) 逐段归一化
        seg_files, seg_durs = [], []
        for i, seg in enumerate(segs):
            src = resolve(seg["file"], base)
            if not os.path.exists(src):
                sys.exit(f"ERROR: 素材不存在: {src}")
            out = os.path.join(work, f"seg_{i}.mp4")
            do_sil = seg.get("strip_silence", do_silence_global)
            log(f"处理片段 {i}: {os.path.basename(src)} "
                f"trim={seg.get('trim')} silence={do_sil}")
            process_segment(ffmpeg, ffprobe, src, out, tw, th, crop_mode,
                            do_sil, db, smin, seg.get("trim"))
            seg_files.append(out)
            seg_durs.append(duration_of(ffprobe, out))
        log(f"各段时长: {[round(d,2) for d in seg_durs]}")

        # 2) 混流 + 转场 + CTA + BGM
        cta = plan.get("cta")
        bgm = plan.get("bgm")
        bgm_file = resolve(bgm["file"], base) if bgm else None
        # CJK 字体：复制到 work 下，避免 filtergraph 里的盘符冒号问题
        fontfile_rel = None
        real_font = find_cjk_font()
        if real_font:
            dst = os.path.join(work, "cjk" + os.path.splitext(real_font)[1])
            shutil.copy(real_font, dst)
            fontfile_rel = os.path.basename(dst)
        tmp_mp4 = os.path.join(work, "tmp.mp4")
        total, trans_win = build_concat(ffmpeg, seg_files, seg_durs, plan, work,
                                        tmp_mp4, fontfile_rel, cta, bgm_file)
        log(f"混流完成，总时长 {total:.2f}s")

        # 3) 字幕
        sub = plan.get("subtitle", {})
        sub_on = sub.get("enabled", True)
        final = os.path.join(work, "final.mp4")
        if sub_on:
            wbin, wmode = find_whisper(plan, os.environ)
            if args.whisper:
                p = args.whisper
                bn = os.path.basename(p)
                if bn in ("whisper", "whisper.exe"):
                    wbin, wmode = p, "cli"
                elif _probe(p, "whisper"):
                    wbin, wmode = p, "module"
                elif _probe(p, "faster_whisper"):
                    wbin, wmode = p, "fast"
                else:
                    wbin, wmode = p, "module"
            if not wbin:
                sys.exit("ERROR: 未找到 Whisper 后端。请安装 faster-whisper "
                         "(pip install faster-whisper，轻量) 或 openai-whisper "
                         "(pip install openai-whisper)，也可通过 --whisper / "
                         "plan.whisper_bin / 环境变量 WHISPER_BIN 指定。")
            log(f"Whisper: {wbin} ({wmode})")
            wav = os.path.join(work, "tmp.wav")
            subprocess.run([ffmpeg, "-y", "-i", tmp_mp4, "-vn",
                           "-acodec", "pcm_s16le", "-ar", "16000",
                           "-ac", "1", wav], check=True, cwd=work)
            srt = run_whisper(wbin, wmode, wav,
                              sub.get("model", "base"),
                              sub.get("language", "zh"), work)
            log(f"字幕生成: {os.path.basename(srt)}（需人工修正繁体/错字）")
            style = ("FontSize=30,PrimaryColour=&HFFFFFF,"
                     "OutlineColour=&H000000,Outline=2,Shadow=1,"
                     "Alignment=2,MarginV=40,FontName=Microsoft YaHei")
            burn_subtitles(ffmpeg, work, tmp_mp4,
                           os.path.basename(srt), style, final)
        else:
            log("字幕已关闭，直接交付混流结果")
            shutil.copy(tmp_mp4, final)

        # 3.5) 转场音效（可选）
        sfx_items = (plan.get("sfx") or {}).get("items")
        if sfx_items:
            placements = _sfx_placements(plan, trans_win, total, work,
                                         ffmpeg, base)
            if placements:
                log(f"叠加转场音效 {len(placements)} 个")
                sfx_out = os.path.join(work, "final_sfx.mp4")
                apply_sfx(ffmpeg, final, placements, total, sfx_out, work)
                final = sfx_out
            else:
                log("未解析出有效音效项，跳过")

        # 4) 输出
        output = plan.get("output", "final.mp4")
        output = output if os.path.isabs(output) else os.path.join(base, output)
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        shutil.move(final, output)
        log(f"成品已生成: {output}")
        log("提醒：Whisper(base) 中文转录必有繁体/错字，口播字幕请逐句人工修正。")
        print(f"OUTPUT:{output}")
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            log(f"临时目录保留: {work}")


if __name__ == "__main__":
    main()
