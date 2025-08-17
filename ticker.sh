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
while getopts "gsa:rd-:" opt; do
  case ${opt} in
    g)
      DISPLAY_METALS=true
      ;;
    a)
      ALERT_TIMEFRAME="$OPTARG"
      ;;
    r)
      RATIONALE_FLAG=1
      ;;
    d)
      DEBUG_FLAG=1
      ;;
    -)
      case "$OPTARG" in
        alert)
          # read next arg as timeframe
          val="${!OPTIND}"
          ALERT_TIMEFRAME="$val"
          OPTIND=$((OPTIND + 1))
          ;;
        rationale)
          RATIONALE_FLAG=1
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
  echo "Usage: $0 [-gsd] SYMBOL1 SYMBOL2 ..."
      exit 1
      ;;
  esac
done
shift $((OPTIND -1))
SYMBOLS+=("$@")
DEBUG_FLAG=${DEBUG_FLAG:-0}
RATIONALE_FLAG=${RATIONALE_FLAG:-0}

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
ALERT_DELAY=${ALERT_DELAY:-4}

# Create session directory for cookies if it doesn't exist
[ ! -d "$SESSION_DIR" ] && mkdir -m 700 "$SESSION_DIR"

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
  tmpfile=$(mktemp "${TMPDIR%/}/ticker_output.XXXXXX")
  {
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

        # Write percent, symbol, and formatted line to tmpfile
        printf "%.2f\t%s\t%s\n" "$percentChange" "$symbol" "$line"
      ) &
    done
    wait
  } > "$tmpfile"

  sort -t$'\t' -k1,1nr "$tmpfile" -o "$tmpfile"

  # Sequentially call AI helper (if requested) to control rate
  while IFS=$'\t' read -r percent symbol line; do
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
            # show debug output (prettified if jq available) when requested
            if [ "$DEBUG_FLAG" -eq 1 ]; then
              printf "%s\n" "$full_out"
            fi
            # compact result is expected to be the last line
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
      # If rationale requested but missing, try to extract it from debug JSON in full_out
      if [ "$RATIONALE_FLAG" -eq 1 ] && [ -z "$rationale_part" ]; then
        # Try robust extraction: first look for unescaped JSON "rationale": "..."
        # If not found, look for an escaped JSON string containing \"rationale\":\"...\"
        rat=$(printf "%s" "$full_out" | perl -0777 -ne '
          if (/"rationale"\s*:\s*"([^"]+)"/s) { print $1; exit }
          if (/\\"rationale\\"\s*:\s*\\"([^\\"]+)\\"/s) { $s=$1; $s=~s/\\n/ /g; $s=~s/\\"/"/g; print $s; exit }
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
  done < "$tmpfile"
  rm -f "$tmpfile"
else
  # Unsorted mode: Process in parallel and tag each output with its original index.
  # Then sort numerically by index (preserving input order) and remove the index.
  tmpfile=$(mktemp "${TMPDIR%/}/ticker_output.XXXXXX")
  {
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

        # Write index, symbol, and formatted line to tmpfile
        printf "%d\t%s\t%s\n" "$i" "$symbol" "$line"
      ) &
    done
    wait
  } > "$tmpfile"

  sort -t$'\t' -k1,1n "$tmpfile" -o "$tmpfile"

  # Sequentially call AI helper (if requested) to control rate
  while IFS=$'\t' read -r idx symbol line; do
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
          if (/"rationale"\s*:\s*"([^"]+)"/s) { print $1; exit }
          if (/\\"rationale\\"\s*:\s*\\"([^\\"]+)\\"/s) { $s=$1; $s=~s/\\n/ /g; $s=~s/\\"/"/g; print $s; exit }
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
  done < "$tmpfile"
  rm -f "$tmpfile"
fi
