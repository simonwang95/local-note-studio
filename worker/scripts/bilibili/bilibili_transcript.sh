#!/bin/bash
# =============================================================================
# B站视频字幕智能获取脚本 v5.1
# 功能：CC字幕 → AI字幕 → Qwen3-ASR 转录（三级降级）
# 新增：本地目录批量转录（--local-dir）、本地单文件转录（--local-file）、env.local 配置、conda 环境
# 支持：macOS Chrome/Safari/Firefox、WSL Chromium/Edge Cookie
#       多语言AI字幕、CUDA/ROCm/MPS/CPU
# =============================================================================

set -eo pipefail

# ===== 顶层初始化（bash 3.2 兼容） =====
COOKIE_ARGS=()

# ===== 加载本地配置 =====
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_DIR/env.local" ]; then
    source "$PROJECT_DIR/env.local"
fi

# ===== 默认值（env.local 中的值会覆盖这些） =====
BILI_COOKIE_FILE="${BILI_COOKIE_FILE:-${BILIBILI_COOKIES_FILE:-}}"
OUTPUT_DIR="${OUTPUT_DIR:-${BILIBILI_OUTPUT_DIR:-$PROJECT_DIR/notes/_inbox/bilibili}}"
CACHE_DIR="${CACHE_DIR:-$PROJECT_DIR/cache/audio}"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-$PROJECT_DIR/models}"
BROWSER_TYPE="${BROWSER_TYPE:-chromium}"
CONDA_ENV="${CONDA_ENV:-course-whisper}"
ENABLE_OPENCC="${ENABLE_OPENCC:-true}"
FORCE_ASR="${FORCE_ASR:-false}"
BILIBILI_PREFER_WEB_SUBTITLE="${BILIBILI_PREFER_WEB_SUBTITLE:-true}"
BILIBILI_WEB_SUBTITLE_LANGS="${BILIBILI_WEB_SUBTITLE_LANGS:-zh-CN,zh-Hans,zh-Hant,zh-TW,ai-zh,en,ai-en,ja,ai-ja,ko,ai-kr}"

# ===== 解析命令行参数 =====
LOCAL_DIR=""
LOCAL_FILE=""
LOCAL_RECURSIVE=false
VIDEO_URL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --local-dir)
            LOCAL_DIR="$2"
            shift 2
            ;;
        --local-file)
            LOCAL_FILE="$2"
            shift 2
            ;;
        --recursive)
            LOCAL_RECURSIVE=true
            shift
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        *)
            if [ -z "$VIDEO_URL" ]; then
                VIDEO_URL="$1"
            fi
            shift
            ;;
    esac
done

# ===== 创建目录 =====
mkdir -p "$OUTPUT_DIR"
mkdir -p "$CACHE_DIR"
mkdir -p "$MODEL_CACHE_DIR"

# ===== 获取 Python 路径（conda 环境优先） =====
get_python() {
    if command -v conda &>/dev/null && conda env list 2>/dev/null | grep -qw "$CONDA_ENV"; then
        echo "conda"
    elif [ -f "$PROJECT_DIR/.venv/bin/python3" ]; then
        echo "$PROJECT_DIR/.venv/bin/python3"
    else
        echo "python3"
    fi
}

run_python() {
    local py="$(get_python)"
    if [ "$py" = "conda" ]; then
        conda run -n "$CONDA_ENV" python3 "$@"
    else
        "$py" "$@"
    fi
}

# ===== 清理临时文件 =====
cleanup_temp() {
    rm -f "$CACHE_DIR"/bilibili_subtitle*.srt "$CACHE_DIR"/bilibili_ai_subtitle*.srt \
          "$CACHE_DIR"/bilibili_web_subtitle*.txt "$CACHE_DIR"/bilibili_web_subtitle*.json \
          "$CACHE_DIR"/bilibili_audio*.mp3 "$CACHE_DIR"/bilibili_audio*.m4a \
          "$CACHE_DIR"/bilibili_audio*.wav "$CACHE_DIR"/bilibili_audio*.txt \
          "$CACHE_DIR"/.qwen_transcript.txt \
          "$CACHE_DIR"/local_audio*.wav "$CACHE_DIR"/local_audio*.mp3 "$CACHE_DIR"/local_audio*.m4a
    rm -rf "$CACHE_DIR"/local_work_*
}
trap cleanup_temp EXIT

# ===== 工具函数 =====
detect_cookie() {
    local browser="$1"
    local path="$2"
    local label="$3"
    if [ -d "$path" ]; then
        local test_out
        test_out=$(yt-dlp --list-subs --cookies-from-browser "$browser:$path" "$VIDEO_URL" 2>&1 | head -1)
        if echo "$test_out" | grep -q "Extracting"; then
            echo "   ✅ 使用 $label Cookie"
            COOKIE_ARGS=(--cookies-from-browser "$browser:$path")
            return 0
        fi
    fi
    return 1
}

get_cookie_args() {
    COOKIE_ARGS=()

    # 优先使用 env.local 中配置的 Cookie 文件（跨平台最稳定）
    # 相对路径相对于项目根目录解析
    if [ -n "${BILI_COOKIE_FILE:-}" ]; then
        # 解析相对路径
        if [[ "$BILI_COOKIE_FILE" != /* ]]; then
            BILI_COOKIE_FILE="$PROJECT_DIR/$BILI_COOKIE_FILE"
        fi
        if [ -f "$BILI_COOKIE_FILE" ]; then
            echo "   ✅ 使用 Cookie 文件: $BILI_COOKIE_FILE"
            COOKIE_ARGS=(--cookies "$BILI_COOKIE_FILE")
            return
        else
            echo "   ⚠️  Cookie 文件不存在: $BILI_COOKIE_FILE"
        fi
    fi

    # 检测操作系统以选择正确的浏览器 Cookie 路径
    local os_type="linux"
    if [[ "$(uname -s)" == "Darwin" ]]; then
        os_type="macos"
    elif [[ -d "/mnt/c/Users" ]]; then
        os_type="wsl"
    fi

    case "$BROWSER_TYPE" in
        chrome)
            if [ "$os_type" = "macos" ]; then
                detect_cookie "chrome" "$HOME/Library/Application Support/Google/Chrome" "macOS Chrome" || true
            fi
            ;;
        chromium)
            if [ "$os_type" = "macos" ]; then
                detect_cookie "chromium" "$HOME/Library/Application Support/Chromium" "macOS Chromium" || true
            else
                detect_cookie "chromium" "$HOME/snap/chromium/common/chromium" "WSL Chromium" || true
            fi
            ;;
        edge)
            if [ "$os_type" = "macos" ]; then
                detect_cookie "edge" "$HOME/Library/Application Support/Microsoft Edge" "macOS Edge" || true
            elif [ "$os_type" = "wsl" ]; then
                local win_user
                win_user=$(ls /mnt/c/Users/ 2>/dev/null | grep -v "Public\|Default\|All Users" | head -1)
                if [ -n "$win_user" ]; then
                    detect_cookie "edge" "C:/Users/$win_user/AppData/Local/Microsoft/Edge/User Data" "Windows Edge" || true
                fi
            fi
            ;;
        safari)
            if [ "$os_type" = "macos" ]; then
                detect_cookie "safari" "$HOME/Library/Safari" "macOS Safari" || true
            fi
            ;;
        firefox)
            if [ "$os_type" = "macos" ]; then
                detect_cookie "firefox" "$HOME/Library/Application Support/Firefox" "macOS Firefox" || true
            else
                detect_cookie "firefox" "$HOME/snap/firefox/common/.mozilla/firefox" "WSL Firefox" || true
            fi
            ;;
    esac

    # 首次未找到，尝试所有已知浏览器（按平台）
    if [ ${#COOKIE_ARGS[@]} -eq 0 ]; then
        if [ "$os_type" = "macos" ]; then
            detect_cookie "chrome" "$HOME/Library/Application Support/Google/Chrome" "macOS Chrome" || \
            detect_cookie "chromium" "$HOME/Library/Application Support/Chromium" "macOS Chromium" || \
            detect_cookie "edge" "$HOME/Library/Application Support/Microsoft Edge" "macOS Edge" || \
            detect_cookie "firefox" "$HOME/Library/Application Support/Firefox" "macOS Firefox" || \
            detect_cookie "safari" "$HOME/Library/Safari" "macOS Safari" || true
        else
            detect_cookie "chromium" "$HOME/snap/chromium/common/chromium" "WSL Chromium" || \
            { local win_user; win_user=$(ls /mnt/c/Users/ 2>/dev/null | grep -v "Public\|Default\|All Users" | head -1); \
              [ -n "$win_user" ] && detect_cookie "edge" "C:/Users/$win_user/AppData/Local/Microsoft/Edge/User Data" "Windows Edge"; } || \
            detect_cookie "firefox" "$HOME/snap/firefox/common/.mozilla/firefox" "WSL Firefox" || true
        fi
    fi
}

to_safe_name() {
    python3 -c "import sys, re, unicodedata; s=unicodedata.normalize('NFC', sys.stdin.read().strip()); s=re.sub(r'[\\\\/:*?\"<>|]', '', s); s=re.sub(r'[\s\W]+', '-', s); s=re.sub(r'-+', '-', s).strip('-'); print(s[:60] or 'untitled')"
}

to_simplified() {
    if [ "$ENABLE_OPENCC" = "true" ] && command -v opencc >/dev/null 2>&1; then
        opencc -c tw2s
    else
        cat
    fi
}

extract_srt_text() {
    local srt_file="$1"
    awk '
        { sub(/\r$/, "") }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*[0-9]+[[:space:]]*$/ { next }
        /-->/ { next }
        { print }
    ' "$srt_file"
}

normalize_existing_path() {
    local path="$1"
    if [ -e "$path" ]; then
        echo "$path"
        return 0
    fi
    if [[ "$path" != /* ]] && [ -e "/$path" ]; then
        echo "/$path"
        return 0
    fi
    echo "$path"
}

find_local_srt() {
    local media_path="$1"
    local media_dir media_file media_base media_safe exact candidate candidate_name candidate_base candidate_media_base candidate_safe

    media_dir=$(dirname "$media_path")
    media_file=$(basename "$media_path")
    media_base="${media_file%.*}"
    media_safe=$(printf "%s" "$media_base" | to_safe_name)

    exact="${media_dir}/${media_base}.srt"
    if [ -f "$exact" ] && [ -s "$exact" ]; then
        echo "$exact"
        return 0
    fi

    while IFS= read -r candidate; do
        candidate_name=$(basename "$candidate")
        candidate_base="${candidate_name%.srt}"
        case "$candidate_base" in
            "$media_base"_*)
                if [ -s "$candidate" ]; then
                    echo "$candidate"
                    return 0
                fi
                ;;
        esac
        candidate_media_base="${candidate_base%_*}"
        if [ "$candidate_media_base" != "$candidate_base" ]; then
            candidate_safe=$(printf "%s" "$candidate_media_base" | to_safe_name)
            if [ "$candidate_safe" = "$media_safe" ] && [ -s "$candidate" ]; then
                echo "$candidate"
                return 0
            fi
        fi
    done < <(find "$media_dir" -maxdepth 1 -type f ! -name ".*" -iname "*.srt" 2>/dev/null | sort)

    return 1
}

run_asr_transcribe() {
    local audio_file="$1"
    local output_file="$2"
    local engine="${ASR_ENGINE:-qwen3}"
    audio_file=$(normalize_existing_path "$audio_file")

    export HF_HOME="$MODEL_CACHE_DIR"

    local transcribe_py="$(get_python)"

    if [ "$engine" = "whisper" ]; then
        # === Whisper (MLX) ===
        local wh_script="$SCRIPT_DIR/whisper_transcribe.py"
        if [ ! -f "$wh_script" ]; then
            echo "   ❌ 未找到 whisper_transcribe.py"
            return 1
        fi

        local model_path="${ASR_LOCAL_MODEL:-}"
        if [ -z "$model_path" ]; then
            echo "   ❌ 请在 env.local 中设置 ASR_LOCAL_MODEL 指向 Whisper 模型路径"
            return 1
        fi
        if [[ "$model_path" != /* ]]; then
            model_path="$PROJECT_DIR/$model_path"
        fi
        if [ ! -d "$model_path" ]; then
            echo "   ❌ Whisper 模型路径不存在: $model_path"
            return 1
        fi

        echo "   🎤 Whisper (MLX): $model_path"

        local lang_arg=()
        if [ -n "${ASR_LANGUAGE:-}" ]; then
            lang_arg=(--language "$ASR_LANGUAGE")
            echo "   🌐 语言: $ASR_LANGUAGE"
        fi
        local prompt_arg=()
        if [ -n "${ASR_PROMPT:-}" ]; then
            prompt_arg=(--prompt "$ASR_PROMPT")
            echo "   💡 Whisper 提示: ${ASR_PROMPT:0:80}"
        fi
        local progress_arg=()
        if [ -n "${ASR_PROGRESS_INTERVAL:-}" ]; then
            progress_arg=(--progress-interval "$ASR_PROGRESS_INTERVAL")
        fi

        if [ "$transcribe_py" = "conda" ]; then
            conda run -n "$CONDA_ENV" python3 "$wh_script" \
                --audio "$audio_file" --output-file "$output_file" \
                --model-path "$model_path" "${lang_arg[@]}" "${prompt_arg[@]}" "${progress_arg[@]}"
        else
            "$transcribe_py" "$wh_script" \
                --audio "$audio_file" --output-file "$output_file" \
                --model-path "$model_path" "${lang_arg[@]}" "${prompt_arg[@]}" "${progress_arg[@]}"
        fi

    else
        # === Qwen3-ASR（默认） ===
        export ASR_LOCAL_MODEL="${ASR_LOCAL_MODEL:-}"
        local q3_script="$SCRIPT_DIR/qwen3_transcribe.py"
        local extra_args=()

        if [ -n "${ASR_LOCAL_MODEL:-}" ]; then
            local resolved_model="$ASR_LOCAL_MODEL"
            if [[ "$resolved_model" != /* ]]; then
                resolved_model="$PROJECT_DIR/$resolved_model"
            fi
            if [ -d "$resolved_model" ]; then
                extra_args=(--local-model "$resolved_model")
                echo "   🎤 使用本地模型: $resolved_model"
            fi
        fi
        if [ "${FORCE_ASR_CPU:-false}" = "true" ]; then
            extra_args+=(--force-cpu)
        fi

        if [ "$transcribe_py" = "conda" ]; then
            conda run -n "$CONDA_ENV" python3 "$q3_script" --audio "$audio_file" --output-file "$output_file" "${extra_args[@]}"
        else
            "$transcribe_py" "$q3_script" --audio "$audio_file" --output-file "$output_file" "${extra_args[@]}"
        fi
    fi
}

# ===== 单个视频转录（B站 URL） =====
transcribe_bilibili_url() {
    local url="$1"

    echo "🔍 正在获取视频信息..."

    get_cookie_args

    if [ ${#COOKIE_ARGS[@]} -eq 0 ]; then
        echo "   ⚠️ 无可用Cookie，B站AI字幕可能无法获取"
        echo "   💡 请先用浏览器登录 bilibili.com"
        echo "      macOS: Chrome / Chromium / Edge / Safari / Firefox"
        echo "      Linux: chromium-browser"
    else
        local cookie_age
        local cp
        for cp in \
            "$HOME/Library/Application Support/Google/Chrome/Default/Cookies" \
            "$HOME/Library/Application Support/Chromium/Default/Cookies" \
            "$HOME/Library/Application Support/Microsoft Edge/Default/Cookies" \
            "$HOME/snap/chromium/common/chromium/Default/Cookies"; do
            if [ -f "$cp" ]; then
                cookie_age=$(ls -lu "$cp" 2>/dev/null | awk '{print $6, $7}')
                [ -n "$cookie_age" ] && echo "   ℹ️  Cookie最后使用: $cookie_age（约30天过期）"
                break
            fi
        done
    fi
    echo ""

    # 获取视频元数据
    local video_info
    video_info=$( { yt-dlp "${COOKIE_ARGS[@]}" --dump-json "$url" 2>/dev/null || true; } | head -1)

    if [ -z "$video_info" ]; then
        video_info=$( { yt-dlp --dump-json "$url" 2>/dev/null || true; } | head -1)
        if [ -z "$video_info" ]; then
            echo "❌ 无法获取视频信息，请检查网络或链接是否正确"
            return 1
        fi
    fi

    extract_json() {
        echo "$video_info" | python3 -c "import sys, json; print(json.load(sys.stdin).get('$1', '$2'))"
    }

    local TITLE;       TITLE=$(extract_json "title" "未知标题")
    local AUTHOR;      AUTHOR=$(extract_json "uploader" "未知作者")
    local UPLOAD_DATE; UPLOAD_DATE=$(extract_json "upload_date" "未知时间")
    local DURATION_SEC;DURATION_SEC=$(extract_json "duration" "0")
    local VIDEO_ID;    VIDEO_ID=$(extract_json "id" "")

    # duration 字段可能是浮点数，bash 算术不支持小数，先取整
    DURATION_SEC=$(printf "%.0f" "$DURATION_SEC" 2>/dev/null || echo "0")
    local DURATION="$((DURATION_SEC / 60))分$((DURATION_SEC % 60))秒"

    local UPLOAD_DATE_FORMATTED="$UPLOAD_DATE"
    if [ "$UPLOAD_DATE" != "未知时间" ]; then
        UPLOAD_DATE_FORMATTED=$(echo "$UPLOAD_DATE" | sed 's/\(....\)\(..\)\(..\)/\1-\2-\3/')
    fi

    echo "📹 视频: $TITLE"
    echo "👤 作者: $AUTHOR"
    echo "📅 发布: $UPLOAD_DATE_FORMATTED"
    echo "⏱️  时长: $DURATION"

    # ===== 三级降级转录 =====
    echo ""

    # 初始化（FORCE_ASR=true 时也需要）
    local HAS_CC_SUBS=false CC_SUB_LANG=""
    local HAS_AI_SUBS=false AI_LANG=""

    if [ "$FORCE_ASR" = "true" ]; then
        echo "⚡ FORCE_ASR=true，跳过字幕检测，直接使用 Qwen3-ASR 本地转录"
    else
        echo "🔍 正在检查字幕..."

        local SUB_CHECK
        SUB_CHECK=$(yt-dlp "${COOKIE_ARGS[@]}" --list-subs "$url" 2>&1)

        CC_SUB_LANG=$(echo "$SUB_CHECK" | awk '!/danmaku/ && !/ai-/ && /^[[:space:]]*(zh-CN|zh-TW|zh-Hans|zh-Hant|en|ja|ko|es|ar|pt|de|fr)($|[-[:space:]])/ {print $1; exit}')
        [ -n "$CC_SUB_LANG" ] && HAS_CC_SUBS=true

        for lang in "ai-zh" "ai-en" "ai-ja" "ai-kr" "ai-th" "ai-id" "ai-vi"; do
            if echo "$SUB_CHECK" | grep -q "$lang"; then
                HAS_AI_SUBS=true
                AI_LANG="$lang"
                break
            fi
        done
    fi

    local TRANSCRIPT_SOURCE=""
    local TRANSCRIPT_TEXT=""

    # 第0级：网页播放器实际字幕
    if [ "$BILIBILI_PREFER_WEB_SUBTITLE" = "true" ] && [ "$FORCE_ASR" != "true" ]; then
        echo "🔍 尝试获取网页播放器字幕..."
        local web_subtitle_file="${CACHE_DIR}/bilibili_web_subtitle.txt"
        local web_subtitle_meta="${CACHE_DIR}/bilibili_web_subtitle.json"
        local web_cookie_args=()
        if [ -n "${BILI_COOKIE_FILE:-}" ] && [ -f "$BILI_COOKIE_FILE" ]; then
            web_cookie_args=(--cookies "$BILI_COOKIE_FILE")
        fi
        if run_python "$SCRIPT_DIR/fetch_web_subtitle.py" "$url" \
            "${web_cookie_args[@]}" \
            --preferred-langs "$BILIBILI_WEB_SUBTITLE_LANGS" \
            --output "$web_subtitle_file" \
            --meta "$web_subtitle_meta"; then
            if [ -s "$web_subtitle_file" ]; then
                local web_label
                web_label=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("lan_doc") or d.get("lan") or "unknown")' "$web_subtitle_meta" 2>/dev/null || echo "unknown")
                echo "✅ 网页播放器字幕获取成功（$web_label）"
                TRANSCRIPT_SOURCE="B站网页播放器字幕 ($web_label)"
                TRANSCRIPT_TEXT=$(cat "$web_subtitle_file")
            fi
        else
            echo "⚠️  网页播放器字幕获取失败，回落到 yt-dlp 字幕检测..."
        fi
    fi

    # 第1级：人工CC字幕
    if [ -z "$TRANSCRIPT_TEXT" ] && [ "$HAS_CC_SUBS" = true ] && [ "$FORCE_ASR" != "true" ]; then
        echo "✅ 发现人工CC字幕（$CC_SUB_LANG），优先下载..."
        yt-dlp "${COOKIE_ARGS[@]}" --skip-download --write-subs --sub-langs "$CC_SUB_LANG" --convert-subs srt \
            -o "${CACHE_DIR}/bilibili_subtitle.%(ext)s" "$url" 2>&1

        local sub_file
        sub_file=$(find "$CACHE_DIR" -maxdepth 1 -name "bilibili_subtitle*.srt" -type f 2>/dev/null | head -1)

        if [ -n "$sub_file" ] && [ -s "$sub_file" ]; then
            echo "✅ CC字幕下载成功"
            TRANSCRIPT_SOURCE="B站CC字幕"
            TRANSCRIPT_TEXT=$(sed '/^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/d' "$sub_file" | sed '/^[0-9]*$/d' | sed '/^$/d')
        else
            echo "⚠️  CC字幕下载失败..."
            HAS_CC_SUBS=false
        fi
    fi

    # 第2级：AI字幕
    if [ -z "$TRANSCRIPT_TEXT" ] && [ "$HAS_AI_SUBS" = true ] && [ "$FORCE_ASR" != "true" ]; then
        echo "✅ 发现AI字幕（$AI_LANG），正在下载..."
        yt-dlp "${COOKIE_ARGS[@]}" --skip-download --write-subs --write-auto-subs --sub-langs "$AI_LANG" --convert-subs srt \
            -o "${CACHE_DIR}/bilibili_ai_subtitle.%(ext)s" "$url" 2>&1

        local sub_file
        sub_file=$(find "$CACHE_DIR" -maxdepth 1 -name "bilibili_ai_subtitle*.srt" -type f 2>/dev/null | head -1)

        if [ -n "$sub_file" ] && [ -s "$sub_file" ]; then
            echo "✅ AI字幕下载成功"
            TRANSCRIPT_SOURCE="B站AI字幕 ($AI_LANG)"
            TRANSCRIPT_TEXT=$(sed '/^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/d' "$sub_file" | sed '/^[0-9]*$/d' | sed '/^$/d')
        else
            echo "⚠️  AI字幕下载失败..."
            HAS_AI_SUBS=false
        fi
    fi

    # 第2.5级：AI字幕兜底（FORCE_ASR 模式下跳过）
    if [ -z "$TRANSCRIPT_TEXT" ] && [ "$FORCE_ASR" != "true" ]; then
        echo "🔍 尝试直接下载 AI 字幕（兜底）..."
        for try_lang in "ai-zh" "ai-en" "ai-ja"; do
            yt-dlp "${COOKIE_ARGS[@]}" --skip-download --write-subs --write-auto-subs --sub-langs "$try_lang" --convert-subs srt \
                -o "${CACHE_DIR}/bilibili_ai_subtitle.%(ext)s" "$url" 2>/dev/null
            local sub_file
            sub_file=$(find "$CACHE_DIR" -maxdepth 1 -name "bilibili_ai_subtitle*.srt" -type f 2>/dev/null | head -1)
            if [ -n "$sub_file" ] && [ -s "$sub_file" ]; then
                echo "✅ 兜底成功！AI字幕已下载（$try_lang）"
                TRANSCRIPT_SOURCE="B站AI字幕 ($try_lang)"
                TRANSCRIPT_TEXT=$(sed '/^[0-9][0-9]:[0-9][0-9]:[0-9][0-9]/d' "$sub_file" | sed '/^[0-9]*$/d' | sed '/^$/d')
                break
            fi
        done
    fi

    # 第3级：Qwen3-ASR
    if [ -z "$TRANSCRIPT_TEXT" ]; then
        echo "🎤 未发现字幕，使用 Qwen3-ASR 本地语音转文字..."
        echo "⏳ 这可能需要一些时间，请耐心等待..."

        echo "   ⬇️ 下载音频..."
        yt-dlp "${COOKIE_ARGS[@]}" -x --audio-format mp3 -o "${CACHE_DIR}/bilibili_audio.%(ext)s" "$url" 2>&1 || \
        yt-dlp -x --audio-format mp3 -o "${CACHE_DIR}/bilibili_audio.%(ext)s" "$url" 2>&1

        local audio_file
        audio_file=$(find "$CACHE_DIR" -maxdepth 1 \( -name "bilibili_audio*.mp3" -o -name "bilibili_audio*.m4a" \) 2>/dev/null | head -1)

        if [ -z "$audio_file" ]; then
            echo "❌ 音频下载失败"
            return 1
        fi

        echo "   🔄 音频格式优化（16kHz 单声道）..."
        local wav_file="${CACHE_DIR}/bilibili_audio.wav"
        ffmpeg -y -i "$audio_file" -ar 16000 -ac 1 "$wav_file" 2>/dev/null

        if [ -f "$wav_file" ] && [ -s "$wav_file" ]; then
            audio_file="$wav_file"
            echo "   ✅ 音频已优化"
        fi

        local q3_output="${CACHE_DIR}/.qwen_transcript.txt"
        echo "   🎤 开始语音转文字..."
        run_asr_transcribe "$audio_file" "$q3_output"

        if [ -f "$q3_output" ] && [ -s "$q3_output" ]; then
            TRANSCRIPT_SOURCE=$(head -1 "$q3_output")
            TRANSCRIPT_TEXT=$(tail -n +2 "$q3_output")
            rm -f "$q3_output"
            echo "✅ 转录完成"
        else
            echo "❌ Qwen3-ASR 转录失败"
            rm -f "$q3_output"
            return 1
        fi
    fi

    # 繁体转简体
    TRANSCRIPT_TEXT_SIMPLIFIED=$(echo "$TRANSCRIPT_TEXT" | to_simplified)

    # 按当前时间组织输出目录（YYYY-MM 格式），文件名保留视频发布时间
    local TODAY; TODAY=$(date '+%Y-%m')
    local final_outdir="${OUTPUT_DIR}/${TODAY}"
    mkdir -p "$final_outdir"

    local SAFE_TITLE;  SAFE_TITLE=$(echo "$TITLE" | to_safe_name)
    local AUTHOR_SAFE; AUTHOR_SAFE=$(echo "$AUTHOR" | to_safe_name)
    local OUTPUT_FILE="${final_outdir}/${SAFE_TITLE}_${AUTHOR_SAFE}_${UPLOAD_DATE_FORMATTED}_${VIDEO_ID}.md"

    write_output_file "$OUTPUT_FILE" "$TITLE" "$url" "$AUTHOR" "$UPLOAD_DATE_FORMATTED" "$DURATION" "$TRANSCRIPT_SOURCE" "$TRANSCRIPT_TEXT_SIMPLIFIED"

    echo ""
    echo "✅ 转录完成！"
    echo "📄 文件已保存: $OUTPUT_FILE"
    echo "$OUTPUT_FILE"
}

# ===== 本地文件转录 =====
transcribe_local_file() {
    local file_path="$1"
    local file_index="${2:-}"
    local file_total="${3:-}"
    file_path=$(normalize_existing_path "$file_path")
    local filename
    filename=$(basename "$file_path")
    local file_label="$filename"
    if [ -n "$file_index" ] && [ -n "$file_total" ]; then
        file_label="[$file_index/$file_total] $filename"
    fi

    echo "🎬 本地文件: $file_label"

    # 提取音频（如果是视频文件）
    local ext="${filename##*.}"
    ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    local video_exts="mp4 mkv avi mov webm flv wmv ts"

    local audio_input="$file_path"
    local base_name="${filename%.*}"
    local SAFE_NAME; SAFE_NAME=$(echo "$base_name" | to_safe_name)
    local NOW; NOW=$(date '+%Y-%m-%d')
    local LOCAL_OUT="${OUTPUT_DIR}/local"
    mkdir -p "$LOCAL_OUT"

    # 去重：已有 Markdown 时直接返回，避免重复执行 Whisper/ASR。
    local EXISTING
    EXISTING=$(find "$LOCAL_OUT" -maxdepth 1 -name "${SAFE_NAME}_*.md" -type f 2>/dev/null | head -1)
    if [ -n "$EXISTING" ]; then
        echo "   ⏭️  $file_label: 已存在转录文件: $(basename "$EXISTING")，跳过 ASR"
        echo "$EXISTING"
        return 0
    fi

    # FORCE_ASR=false 时，优先使用同目录同名 .srt 字幕，避免不必要的 ASR。
    local subtitle_file=""
    subtitle_file=$(find_local_srt "$file_path" || true)

    if [ "${FORCE_ASR:-false}" != "true" ] && [ -n "$subtitle_file" ]; then
        echo "   📝 $file_label: 发现同名 SRT 字幕，优先使用: $(basename "$subtitle_file")"
        local subtitle_text
        subtitle_text=$(extract_srt_text "$subtitle_file" | to_simplified)

        if [ -n "$subtitle_text" ]; then
            local subtitle_output="${LOCAL_OUT}/${SAFE_NAME}_${NOW}.md"
            echo "   📝 $file_label: 写入 Markdown: $(basename "$subtitle_output")"
            write_output_file "$subtitle_output" "$base_name" "file://$file_path" "本地文件" "$NOW" "未知" "本地SRT字幕" "$subtitle_text"
            echo "   ✅ $file_label: 字幕导入完成 → $(basename "$subtitle_output")"
            echo "$subtitle_output"
            return 0
        fi

        echo "   ⚠️  $file_label: 同名 SRT 字幕为空，回落到 ASR"
    fi

    local work_id="${$}_${file_index:-0}_${RANDOM}"
    local work_dir="${CACHE_DIR}/local_work_${work_id}"
    mkdir -p "$work_dir"

    if echo "$video_exts" | grep -qw "$ext"; then
        echo "   🎬 $file_label: 检测到视频格式，提取音频..."
        local audio_out="${work_dir}/audio.wav"
        rm -f "$audio_out"
        ffmpeg -y -i "$file_path" -vn -ar 16000 -ac 1 "$audio_out" 2>/dev/null
        if [ -f "$audio_out" ] && [ -s "$audio_out" ]; then
            audio_input="$audio_out"
            echo "   ✅ $file_label: 音频已提取"
        else
            echo "   ⚠️  $file_label: ffmpeg 提取音频失败，尝试直接输入"
        fi
    elif [ "$ext" = "wav" ]; then
        # 检查是否需要重新采样
        local sample_rate
        sample_rate=$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of default=noprint_wrappers=1:nokey=1 "$file_path" 2>/dev/null)
        local channels
        channels=$(ffprobe -v error -select_streams a:0 -show_entries stream=channels -of default=noprint_wrappers=1:nokey=1 "$file_path" 2>/dev/null)
        if [ "$sample_rate" != "16000" ] || [ "$channels" != "1" ]; then
            echo "   🔄 $file_label: 音频格式优化（16kHz 单声道）..."
            local wav_out="${work_dir}/audio.wav"
            rm -f "$wav_out"
            ffmpeg -y -i "$file_path" -ar 16000 -ac 1 "$wav_out" 2>/dev/null
            if [ -f "$wav_out" ] && [ -s "$wav_out" ]; then
                audio_input="$wav_out"
                echo "   ✅ $file_label: 音频已优化"
            else
                echo "   ⚠️  $file_label: 音频格式优化失败，尝试直接输入"
            fi
        else
            echo "   ✅ $file_label: WAV 已是 16kHz 单声道，无需转换"
        fi
    else
        # 其他音频格式统一转换
        echo "   🔄 $file_label: 音频格式优化（16kHz 单声道）..."
        local wav_out="${work_dir}/audio.wav"
        rm -f "$wav_out"
        ffmpeg -y -i "$file_path" -ar 16000 -ac 1 "$wav_out" 2>/dev/null
        if [ -f "$wav_out" ] && [ -s "$wav_out" ]; then
            audio_input="$wav_out"
            echo "   ✅ $file_label: 音频已优化"
        else
            echo "   ⚠️  $file_label: 音频格式优化失败，尝试直接输入"
        fi
    fi

    # 本地 ASR 转录
    echo "   🎤 $file_label: 开始语音转文字..."
    local q3_output="${work_dir}/transcript.txt"
    rm -f "$q3_output"
    run_asr_transcribe "$audio_input" "$q3_output"

    if [ ! -f "$q3_output" ] || [ ! -s "$q3_output" ]; then
        echo "❌ $file_label: ASR 转录失败"
        rm -rf "$work_dir"
        return 1
    fi

    local TRANSCRIPT_SOURCE; TRANSCRIPT_SOURCE=$(head -1 "$q3_output")
    local TRANSCRIPT_TEXT;    TRANSCRIPT_TEXT=$(tail -n +2 "$q3_output")
    rm -rf "$work_dir"

    # 繁体转简体
    TRANSCRIPT_TEXT=$(echo "$TRANSCRIPT_TEXT" | to_simplified)

    # 生成输出文件
    local OUTPUT_FILE="${LOCAL_OUT}/${SAFE_NAME}_${NOW}.md"

    echo "   📝 $file_label: 写入 Markdown: $(basename "$OUTPUT_FILE")"
    write_output_file "$OUTPUT_FILE" "$base_name" "file://$file_path" "本地文件" "$NOW" "未知" "$TRANSCRIPT_SOURCE" "$TRANSCRIPT_TEXT"

    echo "   ✅ $file_label: 转录完成 → $(basename "$OUTPUT_FILE")"
    echo "$OUTPUT_FILE"
}

write_output_file() {
    local out="$1" title="$2" link="$3" author="$4" date="$5" duration="$6" source="$7" text="$8"
    local include_full="${INCLUDE_FULL_TEXT:-false}"

    cat > "$out" << EOF
# $title

> **链接**：$link
> **作者**：$author
> **发布时间**：$date
> **视频时长**：$duration
> **转录来源**：$source
> **转录时间**：$(date '+%Y-%m-%d %H:%M:%S')

---

## 视频摘要

【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成结构化摘要】

---

## 思维导图

【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成思维导图】

---

## AI校对

【AI待处理：请设置 SUMMARY_API_KEY 后重新运行以生成校对版本】

---
EOF

    # 完整原文在末尾，默认折叠（LLM 处理时需要，用户查看时可收起）
    if [ "$include_full" = "true" ]; then
        cat >> "$out" << EOF

## 完整原文

$text
EOF
    else
        cat >> "$out" << EOF

<details>
<summary>📄 完整原文</summary>

$text

</details>
EOF
    fi
}

# ===== 主入口 =====

# 模式：本地单文件转录
if [ -n "$LOCAL_FILE" ]; then
    LOCAL_FILE=$(normalize_existing_path "$LOCAL_FILE")
    if [ ! -f "$LOCAL_FILE" ]; then
        echo "❌ 文件不存在: $LOCAL_FILE"
        exit 1
    fi

    echo "================================================================================"
    echo "📼 本地单文件转录"
    echo "📄 文件: $LOCAL_FILE"
    echo "================================================================================"

    if transcribe_local_file "$LOCAL_FILE" 1 1; then
        exit 0
    fi
    exit 1
fi

# 模式：本地目录批量转录
if [ -n "$LOCAL_DIR" ]; then
    LOCAL_DIR=$(normalize_existing_path "$LOCAL_DIR")
    if [ ! -d "$LOCAL_DIR" ]; then
        echo "❌ 目录不存在: $LOCAL_DIR"
        exit 1
    fi

    echo "📁 扫描本地目录: $LOCAL_DIR"
    if [ "$LOCAL_RECURSIVE" = "true" ]; then
        echo "   🔁 递归扫描子目录: 已启用"
    fi
    echo ""

    # 支持的媒体格式
    patterns=(-name "*.mp4" -o -name "*.mkv" -o -name "*.avi" -o -name "*.mov" \
              -o -name "*.webm" -o -name "*.flv" -o -name "*.wmv" -o -name "*.ts" \
              -o -name "*.mp3" -o -name "*.m4a" -o -name "*.wav" -o -name "*.flac" \
              -o -name "*.ogg" -o -name "*.opus" -o -name "*.aac")

    if [ "$LOCAL_RECURSIVE" = "true" ]; then
        files=$(find "$LOCAL_DIR" -type f ! -name ".*" \( "${patterns[@]}" \) 2>/dev/null | sort)
    else
        files=$(find "$LOCAL_DIR" -maxdepth 1 -type f ! -name ".*" \( "${patterns[@]}" \) 2>/dev/null | sort)
    fi

    if [ -z "$files" ]; then
        echo "❌ 目录中没有找到支持的媒体文件"
        if [ "$LOCAL_RECURSIVE" != "true" ]; then
            echo "   如需扫描子目录，请加 --recursive"
        fi
        echo "   支持格式: mp4, mkv, avi, mov, webm, flv, wmv, ts, mp3, m4a, wav, flac, ogg, opus, aac"
        exit 1
    fi

    count=$(echo "$files" | wc -l | tr -d ' ')
    echo "📊 找到 $count 个媒体文件"
    echo ""

    success=0 fail=0 current=0
    while IFS= read -r f; do
        current=$((current + 1))
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        if transcribe_local_file "$f" "$current" "$count"; then
            success=$((success + 1))
            echo "✅ [$current/$count] $(basename "$f") 处理成功"
        else
            fail=$((fail + 1))
            echo "❌ [$current/$count] $(basename "$f") 处理失败"
        fi
    done <<< "$files"

    echo ""
    echo "================================================================================"
    echo "📊 批量转录完成: 成功 $success 个, 失败 $fail 个"
    echo "================================================================================"
    exit $((fail > 0 ? 1 : 0))
fi

# 模式：B站 URL 转录
if [ -z "$VIDEO_URL" ]; then
    echo "用法: $0 <B站视频链接> [选项]"
    echo "       $0 --local-file <文件路径> [选项]"
    echo "       $0 --local-dir <目录路径> [选项]"
    echo ""
    echo "选项:"
    echo "  --local-file <文件>  转录单个本地媒体文件"
    echo "  --local-dir <目录>    批量转录本地目录中的媒体文件"
    echo "  --recursive           本地目录模式下递归扫描子目录"
    echo "  --output-dir <目录>   输出目录（默认: $OUTPUT_DIR）"
    echo ""
    echo "配置: 编辑项目根目录的 env.local 文件"
    exit 1
fi

transcribe_bilibili_url "$VIDEO_URL"
