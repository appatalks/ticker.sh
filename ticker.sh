#!/usr/bin/env bash
set -e

#-----------------------------------------------------
# Setup: Locale and temporary session directory for cookies
#-----------------------------------------------------
LANG=C
LC_NUMERIC=C

: ${TMPDIR:=/tmp}
SESSION_DIR="${TMPDIR%/}/ticker.sh-$(whoami)"
COOKIE_FILE="${SESSION_DIR}/cookies.txt"

# Script version (update as appropriate)
VERSION="ticker.sh dev-ai"

#-----------------------------------------------------
# Yahoo Finance API configuration
#-----------------------------------------------------
API_ENDPOINT="https://query1.finance.yahoo.com/v8/finance/chart/"
API_SUFFIX="?interval=1d"

#-----------------------------------------------------
# Colors: Define unless NO_COLOR is set
#-----------------------------------------------------
if [ -z "$NO_COLOR" ]; then
  : "${COLOR_GREEN:=$'\e[32m'}"
  : "${COLOR_RED:=$'\e[31m'}"
  : "${COLOR_RESET:=$'\e[00m'}"
fi

#-----------------------------------------------------
# Flags and symbols
# -g: show precious metals
# -s: sort by gain/loss percentage (if not, show unsorted, but wait for all)
#-----------------------------------------------------
SYMBOLS=()
DISPLAY_METALS=false
SORT_RESULTS=false
ALERT_TIMEFRAME=""
RATIONALE_FLAG=0
# Allow default cleanup behavior to be set via environment variable CLEANUP (true/false)
CLEANUP=${CLEANUP:-true}
if [ "$CLEANUP" = "true" ] || [ "$CLEANUP" = "1" ]; then
  CLEANUP_FLAG=1
else
  CLEANUP_FLAG=0
fi
show_help() {
  cat <<'HELP'
Usage: ./ticker.sh [OPTIONS] SYMBOL1 SYMBOL2 ...

Fetch live stock and metal prices from Yahoo Finance and optionally request
an AI alert per symbol.

Options:
  -g, --metals            Show precious metal spot prices (gold, silver, platinum)
  -s, --sort              Sort stock symbols by percent gain/loss (descending)
  -a TIMEFRAME, --alert TIMEFRAME
                          Request an AI alert for each symbol using the given
                          timeframe (e.g. 1m,5m,15m,1h,1d). The helper script
                          `ai_alert.py` performs indicator calculations and
                          returns a compact recommendation.
  -r, --rationale         Include a short rationale with AI recommendations.
                          If used without -a, this implies -a 1d (default).
  -d, --debug             Print debug output from the AI helper (raw model
                          response and payload preview).
  -C, --cleanup           Remove session cookies and run artifacts after the run
  -n, --no-cleanup        Keep run artifacts (do not remove per-run tempdir)
  -t THREADS, --threads THREADS
                         Override the default THREADS concurrency at runtime
  -h, --help              Show this help message and exit.

AI status markers:
  [S] - The AI helper successfully called the model and returned a recommendation.
  [F] - The AI helper failed to call the model (network or API error) and used
    local fallback heuristics to produce a recommendation. Confidence may be lower.

Confidence scale:
  The AI recommendation includes a confidence score from 1 to 10 (integer).
  10 = highest confidence, 1 = lowest confidence. Use the score to weigh the
  recommendation.

Notes:
  - To use AI alerts you must provide a working `ai_alert.py` next to this
    script and set your OpenAI API key in the environment or a .env file
    (OPENAI_API_KEY). The default model is configurable via OPENAI_MODEL.
  - The script rate-limits AI calls using ALERT_DELAY (default 4s).
  - To disable colored output (useful in logs), set NO_COLOR=1.

Examples:
  # Print prices for symbols in input order
  ./ticker.sh AAPL MSFT GOOG

  # Show metals and symbols together
  ./ticker.sh -g BTC-USD AAPL

  # Request compact AI alerts for each symbol (5 minute timeframe)
  ./ticker.sh -a 5m AAPL MSFT

  # Include a short rationale and show debug info from the helper
  ./ticker.sh -a 5m -r -d AAPL

Note about cleanup:
  By default the script removes per-run AI temp files after the run. Use
  `-C/--cleanup` to also remove the saved cookie file used for Yahoo requests.

HELP
  exit 0
}

show_usage() {
  printf "Usage: %s [OPTIONS] SYMBOL1 SYMBOL2 ...\n" "${0##*/}" >&2
  printf "Try '%s --help' for more information.\n" "${0##*/}" >&2
  exit 1
}

show_version() {
  printf "%s\n" "$VERSION"
  exit 0
}

while getopts "gsa:rdCnt:-hv" opt; do
  case ${opt} in
    g)
      DISPLAY_METALS=true
      ;;
    C)
      CLEANUP_FLAG=1
      ;;
    v)
      show_version
      ;;
    a)
      ALERT_TIMEFRAME="$OPTARG"
      ;;
    r)
      RATIONALE_FLAG=1
      # If the next token looks like a timeframe (e.g., '5m','1h','1d'), treat it as the alert timeframe
      next="${!OPTIND}"
      if [ -n "$next" ] && [ "${next:0:1}" != "-" ]; then
        if printf "%s" "$next" | grep -Eq '^[0-9]+(m|h|d|w|y)$'; then
          ALERT_TIMEFRAME="$next"
          OPTIND=$((OPTIND + 1))
        fi
      fi
      ;;
    d)
      DEBUG_FLAG=1
      ;;
    n)
      # keep run artifacts
      CLEANUP_FLAG=0
      ;;
    t)
      THREADS="$OPTARG"
      ;;
    h)
      show_help
      ;;
    -)
      case "$OPTARG" in
        version)
          show_version
          ;;
        no-cleanup)
          CLEANUP_FLAG=0
          ;;
        threads)
          THREADS_VAL="${!OPTIND}"
          if [ -n "$THREADS_VAL" ] && [ "${THREADS_VAL:0:1}" != "-" ]; then
            THREADS="$THREADS_VAL"
            OPTIND=$((OPTIND + 1))
          fi
          ;;
        alert)
          # read next arg as timeframe
          val="${!OPTIND}"
          ALERT_TIMEFRAME="$val"
          OPTIND=$((OPTIND + 1))
          ;;
        rationale)
          RATIONALE_FLAG=1
          # long-form: if next token is a timeframe, treat as alert timeframe
          val="${!OPTIND}"
          if [ -n "$val" ] && [ "${val:0:1}" != "-" ]; then
            if printf "%s" "$val" | grep -Eq '^[0-9]+(m|h|d|w|y)$'; then
              ALERT_TIMEFRAME="$val"
              OPTIND=$((OPTIND + 1))
            fi
          fi
          ;;
        cleanup)
          CLEANUP_FLAG=1
          ;;
        help)
          show_help
          ;;
        debug)
          DEBUG_FLAG=1
          ;;
        *)
          echo "Unknown option --$OPTARG"
          exit 1
          ;;
      esac
      ;;
    s)
      SORT_RESULTS=true
      ;;
    *)
      show_usage
      ;;
  esac
done
shift $((OPTIND -1))
SYMBOLS+=("$@")
DEBUG_FLAG=${DEBUG_FLAG:-0}
RATIONALE_FLAG=${RATIONALE_FLAG:-0}

# If rationale requested but no timeframe provided, default to 1d
if [ "$RATIONALE_FLAG" -eq 1 ] && [ -z "$ALERT_TIMEFRAME" ]; then
  ALERT_TIMEFRAME="1d"
fi

# Recover from user input like: -a -r 1m SYMBOL  (getopts will set ALERT_TIMEFRAME to '-r')
if [ -n "$ALERT_TIMEFRAME" ] && [ "${ALERT_TIMEFRAME:0:1}" = "-" ]; then
  bad="$ALERT_TIMEFRAME"
  # If there's at least one positional arg, treat it as the intended timeframe
  if [ ${#SYMBOLS[@]} -ge 1 ]; then
    ALERT_TIMEFRAME="${SYMBOLS[0]}"
    # remove the timeframe from symbols
    SYMBOLS=("${SYMBOLS[@]:1}")
  else
    # nothing to recover; unset
    ALERT_TIMEFRAME=""
  fi
  # Now interpret the bad token as a flag: support -r and -d and -s and -g
  case "$bad" in
    -r|--rationale)
      RATIONALE_FLAG=1
      ;;
    -d|--debug)
      DEBUG_FLAG=1
      ;;
    -s|--sort)
      SORT_RESULTS=true
      ;;
    -g|--g)
      DISPLAY_METALS=true
      ;;
    *)
      # ignore unknown
      ;;
  esac
fi

if [ ${#SYMBOLS[@]} -eq 0 ] && [ "$DISPLAY_METALS" = false ]; then
  echo "Usage: $0 [-gsd] SYMBOL1 SYMBOL2 ..."
  exit 1
fi

# If alert timeframe provided, ensure python helper exists
AI_HELPER="$(dirname "$0")/ai_alert.py"
if [ -n "$ALERT_TIMEFRAME" ] && [ ! -x "$AI_HELPER" ]; then
  # Try to make it executable
  [ -f "$AI_HELPER" ] && chmod +x "$AI_HELPER"
fi
# Load environment overrides from a .env file located next to the script, if present.
# Use allexport so variables defined in .env become exported into the script's environment.
ENV_FILE="$(dirname "$0")/.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -o allexport
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  set +o allexport
fi

# Default alert delay (seconds) unless provided via environment or .env
ALERT_DELAY=${ALERT_DELAY:-4}

# Default threads for parallel AI/helper calls (can be overridden in .env)
# Updated default to match .env.example
THREADS=${THREADS:-7}

# Create session directory for cookies if it doesn't exist
[ ! -d "$SESSION_DIR" ] && mkdir -m 700 "$SESSION_DIR"

# Create a per-run temporary directory inside the session dir. Use mktemp
# so concurrent runs won't clobber each other.
RUN_DIR=$(mktemp -d "$SESSION_DIR/run.XXXXXXXX")
umask 077

cleanup_run() {
  # Remove per-run artifacts
  [ -n "$RUN_DIR" ] && rm -rf "$RUN_DIR"
  if [ "$CLEANUP_FLAG" -eq 1 ]; then
    rm -f "$COOKIE_FILE"
  fi
}

trap cleanup_run EXIT

#-----------------------------------------------------
# Function: preflight
# Purpose: Fetch initial cookies from Yahoo Finance
#-----------------------------------------------------
preflight () {
  curl --silent --output /dev/null --cookie-jar "$COOKIE_FILE" "https://finance.yahoo.com" \
    -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8" \
    -H "User-Agent: Chrome/115.0.0.0 Safari/537.36"
}

#-----------------------------------------------------
# Function: fetch_chart
# Purpose: Retrieve JSON chart data for a given symbol
#-----------------------------------------------------
fetch_chart () {
  local symbol=$1
  local url="${API_ENDPOINT}${symbol}${API_SUFFIX}"
  curl --silent -b "$COOKIE_FILE" \
       -H "User-Agent: Chrome/115.0.0.0 Safari/537.36" \
       "$url"
}

# If no cookie file exists, run preflight to get cookies
[ ! -f "$COOKIE_FILE" ] && preflight

#-----------------------------------------------------
# Function: fetch_metal_prices
# Purpose: Retrieve and display precious metal spot prices
#-----------------------------------------------------
fetch_metal_prices () {
  local gold_symbol="GC=F"
  local silver_symbol="SI=F"
  local platinum_symbol="PL=F"

  local gold_price=$(fetch_chart "$gold_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')
  local silver_price=$(fetch_chart "$silver_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')
  local platinum_price=$(fetch_chart "$platinum_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')

  local gold_silver_ratio=$(awk -v gold="$gold_price" -v silver="$silver_price" 'BEGIN {printf "%.2f", gold / silver}')

  local COLOR_YELLOW=$'\e[33m'
  local COLOR_SILVER=$'\e[37m'
  local COLOR_LIGHT_GREY=$'\e[249m'
  local COLOR_BRIGHT_PURPLE=$'\e[35;1m'
  local COLOR_RESET=$'\e[0m'

  echo "Precious Metal Spot Prices:"
  echo "---------------------------"
  printf "${COLOR_YELLOW}Gold Spot:     \$%.2f /OZ${COLOR_RESET}\n" "$gold_price"
  printf "${COLOR_SILVER}Silver Spot:   \$%.2f /OZ${COLOR_RESET}\n" "$silver_price"
  printf "${COLOR_LIGHT_GREY}Platinum Spot: \$%.2f /OZ${COLOR_RESET}\n" "$platinum_price"
  printf "${COLOR_BRIGHT_PURPLE}Gold/Silver Ratio: %.2f${COLOR_RESET}\n" "$gold_silver_ratio"
  echo ""
}

#-----------------------------------------------------
# Display metals if the -g flag is provided
#-----------------------------------------------------
if [ "$DISPLAY_METALS" = true ]; then
  fetch_metal_prices
fi

#-----------------------------------------------------
# Main Processing: Retrieve stock data in parallel.
# We handle two cases:
# 1. Sorted by gain/loss percentage (-s)
# 2. Unsorted: Wait for all jobs and display in input order.
#-----------------------------------------------------
if [ "$SORT_RESULTS" = true ]; then
  # Gather parallel results into a temp file, then sort and process sequentially.
  # Collect results in-memory to avoid tempfiles
  mapfile -t TICKER_OUTPUTS < <(
    for symbol in "${SYMBOLS[@]}"; do
      (
        results=$(fetch_chart "$symbol")
        currentPrice=$(echo "$results" | jq -r '.chart.result[0].meta.regularMarketPrice')
        previousClose=$(echo "$results" | jq -r '.chart.result[0].meta.chartPreviousClose')
        symbol=$(echo "$results" | jq -r '.chart.result[0].meta.symbol')
        [ "$previousClose" = "null" ] && previousClose="1.0"
        priceChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" \
                       'BEGIN {printf "%.2f", currentPrice - previousClose}')
        percentChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" \
                         'BEGIN {printf "%.2f", ((currentPrice - previousClose) / previousClose) * 100}')

        if [ -z "$NO_COLOR" ]; then
          if (( $(echo "$priceChange >= 0" | bc -l) )); then
            color="$COLOR_GREEN"
          else
            color="$COLOR_RED"
          fi
          line=$(printf "%s%-10s%8.2f%10.2f%8s%6.2f%%%s" \
            "$color" "$symbol" "$currentPrice" "$priceChange" "$color" "$percentChange" "$COLOR_RESET")
        else
          line=$(printf "%-10s%8.2f%10.2f%9.2f%%" \
            "$symbol" "$currentPrice" "$priceChange" "$percentChange")
        fi

        # Write percent, symbol, and formatted line to the in-memory array
        printf "%.2f\t%s\t%s\n" "$percentChange" "$symbol" "$line"
      ) &
    done
    wait
  )

  # Sort in-memory by percent change (descending)
  mapfile -t ORDERED_OUTPUTS < <(printf '%s\n' "${TICKER_OUTPUTS[@]}" | sort -t$'\t' -k1,1nr)

  # Sequentially call AI helper (if requested) to control rate
  # If alerts requested, run AI helper calls in parallel with a concurrency limit.
  if [ -n "$ALERT_TIMEFRAME" ]; then
  TMP_AI_DIR="$RUN_DIR/ai"
  mkdir -p "$TMP_AI_DIR"
    ai_index=0
    unset ai_files
    unset ai_lines
    for entry in "${ORDERED_OUTPUTS[@]}"; do
      IFS=$'\t' read -r percent symbol line <<< "$entry"
      ai_lines[$ai_index]="$line"
  out_file="$TMP_AI_DIR/out_$ai_index"
      # Launch helper in background and capture full output to a file
      (
        if [ "$DEBUG_FLAG" -eq 1 ]; then
          if [ "$RATIONALE_FLAG" -eq 1 ]; then
            "$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --debug --rationale 2>/dev/null || true
          else
            "$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --debug 2>/dev/null || true
          fi
        else
          if [ "$RATIONALE_FLAG" -eq 1 ]; then
            "$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --rationale 2>/dev/null || true
          else
            "$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" 2>/dev/null || true
          fi
        fi >"$out_file"
      ) &
      ai_pids[$ai_index]=$!
      ai_files[$ai_index]="$out_file"
      ai_index=$((ai_index + 1))
      # throttle job launches to THREADS
      while [ "$(jobs -rp | wc -l)" -ge "$THREADS" ]; do
        sleep 0.01
      done
    done

    # wait for remaining jobs
    wait

    # Process outputs in original order
    for idx in $(seq 0 $((ai_index - 1))); do
      full_out=$(cat "${ai_files[$idx]}" 2>/dev/null || true)
      if [ "$DEBUG_FLAG" -eq 1 ]; then
        printf "%s\n" "$full_out"
      fi
      ai_out=$(printf "%s" "$full_out" | tail -n1 | tr -d $'\n')
      rec_part="$ai_out"
      status_part=""
      rationale_part=""
      if echo "$ai_out" | grep -q "|"; then
        rec_part=$(echo "$ai_out" | cut -d'|' -f1)
        status_part=$(echo "$ai_out" | cut -d'|' -f2)
        rationale_part=$(echo "$ai_out" | cut -d'|' -f3-)
      fi
      if [ "$RATIONALE_FLAG" -eq 1 ] && [ -z "$rationale_part" ]; then
        rat=$(printf "%s" "$full_out" | perl -0777 -ne '
          if (/"rationale"\s*:\s*"([^\"]+)"/s) { print $1; exit }
          if (/\"rationale\"\s*:\s*\"([^\\\"]+)\"/s) { $s=$1; $s=~s/\\n/ /g; $s=~s/\\"/"/g; print $s; exit }
        ')
        if [ -n "$rat" ]; then
          rationale_part="$rat"
        fi
      fi
      REC_PRINT="$rec_part"
      if [ -z "$NO_COLOR" ]; then
        case "${rec_part%%:*}" in
          BUY)
            REC_PRINT="${COLOR_GREEN}${rec_part}${COLOR_RESET}"
            ;;
          SELL)
            REC_PRINT="${COLOR_RED}${rec_part}${COLOR_RESET}"
            ;;
          *)
            REC_PRINT="$rec_part"
            ;;
        esac
      fi
      line_to_print="${ai_lines[$idx]}"
      if [ -n "$status_part" ]; then
        if [ -n "$rationale_part" ]; then
          printf "%s [%s] [%s] [%s]\n" "$line_to_print" "$REC_PRINT" "$status_part" "$rationale_part"
        else
          printf "%s [%s] [%s]\n" "$line_to_print" "$REC_PRINT" "$status_part"
        fi
      else
        if [ -n "$rationale_part" ]; then
          printf "%s [%s] [%s]\n" "$line_to_print" "$REC_PRINT" "$rationale_part"
        else
          printf "%s [%s]\n" "$line_to_print" "$REC_PRINT"
        fi
      fi
      sleep "$ALERT_DELAY"
    done
  else
    for entry in "${ORDERED_OUTPUTS[@]}"; do
      IFS=$'\t' read -r percent symbol line <<< "$entry"
      printf "%s\n" "$line"
    done
  fi
else
  # Unsorted mode: Process in parallel and tag each output with its original index.
  # Then sort numerically by index (preserving input order) and remove the index.
  # Collect outputs in-memory (preserve original input order by index)
  mapfile -t TICKER_OUTPUTS < <(
    for i in "${!SYMBOLS[@]}"; do
      (
        # Use the index to preserve original ordering.
        symbol="${SYMBOLS[$i]}"
        results=$(fetch_chart "$symbol")
        currentPrice=$(echo "$results" | jq -r '.chart.result[0].meta.regularMarketPrice')
        previousClose=$(echo "$results" | jq -r '.chart.result[0].meta.chartPreviousClose')
        symbol=$(echo "$results" | jq -r '.chart.result[0].meta.symbol')
        [ "$previousClose" = "null" ] && previousClose="1.0"
        priceChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" \
                       'BEGIN {printf "%.2f", currentPrice - previousClose}')
        percentChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" \
                         'BEGIN {printf "%.2f", ((currentPrice - previousClose) / previousClose) * 100}')

        if [ -z "$NO_COLOR" ]; then
          if (( $(echo "$priceChange >= 0" | bc -l) )); then
            color="$COLOR_GREEN"
          else
            color="$COLOR_RED"
          fi
          line=$(printf "%s%-10s%8.2f%10.2f%8s%6.2f%%%s" \
            "$color" "$symbol" "$currentPrice" "$priceChange" "$color" "$percentChange" "$COLOR_RESET")
        else
          line=$(printf "%-10s%8.2f%10.2f%9.2f%%" \
            "$symbol" "$currentPrice" "$priceChange" "$percentChange")
        fi

        # Write index, symbol, and formatted line to the in-memory array
        printf "%d\t%s\t%s\n" "$i" "$symbol" "$line"
      ) &
    done
    wait
  )

  # Order by index to preserve original ordering
  mapfile -t ORDERED_OUTPUTS < <(printf '%s\n' "${TICKER_OUTPUTS[@]}" | sort -t$'\t' -k1,1n)

  # Sequentially call AI helper (if requested) to control rate
  for entry in "${ORDERED_OUTPUTS[@]}"; do
    IFS=$'\t' read -r idx symbol line <<< "$entry"
    if [ -n "$ALERT_TIMEFRAME" ]; then
            # capture full helper output (debug may print multiple JSON blobs)
      if [ "$DEBUG_FLAG" -eq 1 ]; then
              if [ "$RATIONALE_FLAG" -eq 1 ]; then
                full_out=$("$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --debug --rationale 2>/dev/null)
              else
                full_out=$("$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --debug 2>/dev/null)
              fi
            else
              if [ "$RATIONALE_FLAG" -eq 1 ]; then
                full_out=$("$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" --rationale 2>/dev/null)
              else
                full_out=$("$AI_HELPER" "$symbol" "$ALERT_TIMEFRAME" 2>/dev/null)
              fi
            fi
            if [ "$DEBUG_FLAG" -eq 1 ]; then
              printf "%s\n" "$full_out"
            fi
            ai_out=$(printf "%s" "$full_out" | tail -n1 | tr -d $'\n')
      # ai_out expected format REC:CONF or REC:CONF|S or REC:CONF|S|RATIONALE
      rec_part="$ai_out"
      status_part=""
      rationale_part=""
      if echo "$ai_out" | grep -q "|"; then
        rec_part=$(echo "$ai_out" | cut -d'|' -f1)
        status_part=$(echo "$ai_out" | cut -d'|' -f2)
        rationale_part=$(echo "$ai_out" | cut -d'|' -f3-)
      fi
      if [ "$RATIONALE_FLAG" -eq 1 ] && [ -z "$rationale_part" ]; then
        rat=$(printf "%s" "$full_out" | perl -0777 -ne '
          if (/"rationale"\s*:\s*"([^\"]+)"/s) { print $1; exit }
          if (/\"rationale\"\s*:\s*\"([^\\\"]+)\"/s) { $s=$1; $s=~s/\\n/ /g; $s=~s/\\"/"/g; print $s; exit }
        ')
        if [ -n "$rat" ]; then
          rationale_part="$rat"
        fi
      fi
      # Color the recommendation: BUY -> green, SELL -> red (respect NO_COLOR)
      REC_PRINT="$rec_part"
      if [ -z "$NO_COLOR" ]; then
        case "${rec_part%%:*}" in
          BUY)
            REC_PRINT="${COLOR_GREEN}${rec_part}${COLOR_RESET}"
            ;;
          SELL)
            REC_PRINT="${COLOR_RED}${rec_part}${COLOR_RESET}"
            ;;
          *)
            REC_PRINT="$rec_part"
            ;;
        esac
      fi

      if [ -n "$status_part" ]; then
        if [ -n "$rationale_part" ]; then
          printf "%s [%s] [%s] [%s]\n" "$line" "$REC_PRINT" "$status_part" "$rationale_part"
        else
          printf "%s [%s] [%s]\n" "$line" "$REC_PRINT" "$status_part"
        fi
      else
        if [ -n "$rationale_part" ]; then
          printf "%s [%s] [%s]\n" "$line" "$REC_PRINT" "$rationale_part"
        else
          printf "%s [%s]\n" "$line" "$REC_PRINT"
        fi
      fi
      sleep "$ALERT_DELAY"
    else
      printf "%s\n" "$line"
    fi
  done
fi
