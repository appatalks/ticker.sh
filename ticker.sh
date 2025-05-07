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

while getopts "gs" opt; do
  case ${opt} in
    g)
      DISPLAY_METALS=true
      ;;
    s)
      SORT_RESULTS=true
      ;;
    *)
      echo "Usage: $0 [-gs] SYMBOL1 SYMBOL2 ..."
      exit 1
      ;;
  esac
done
shift $((OPTIND -1))
SYMBOLS+=("$@")

if [ ${#SYMBOLS[@]} -eq 0 ] && [ "$DISPLAY_METALS" = false ]; then
  echo "Usage: $0 [-gs] SYMBOL1 SYMBOL2 ..."
  exit 1
fi

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
  # In sorted mode, each job prefixes its output with the numeric percent change.
  # After waiting, we sort numerically (descending) and then remove the key.
  sorted_output=$(
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
            # Build the colored output line
            line=$(printf "%s%-10s%8.2f%10.2f%8s%6.2f%%%s" \
              "$color" "$symbol" "$currentPrice" "$priceChange" "$color" "$percentChange" "$COLOR_RESET")
          else
            line=$(printf "%-10s%8.2f%10.2f%9.2f%%" \
              "$symbol" "$currentPrice" "$priceChange" "$percentChange")
          fi
  
          # Prefix with the percent change as the sort key, then a tab and the output line.
          printf "%.2f\t%s\n" "$percentChange" "$line"
        ) &
      done
      wait
    } | sort -t$'\t' -k1,1nr | cut -f2-
  )
  echo "$sorted_output"
else
  # Unsorted mode: Process in parallel and tag each output with its original index.
  # Then sort numerically by index (preserving input order) and remove the index.
  unsorted_output=$(
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
  
          # Prefix with the original index and a tab, then the output line.
          printf "%d\t%s\n" "$i" "$line"
        ) &
      done
      wait
    } | sort -t$'\t' -k1,1n | cut -f2-
  )
  echo "$unsorted_output"
fi
