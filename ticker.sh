#!/usr/bin/env bash
set -e

LANG=C
LC_NUMERIC=C

: ${TMPDIR:=/tmp}
SESSION_DIR="${TMPDIR%/}/ticker.sh-$(whoami)"
COOKIE_FILE="${SESSION_DIR}/cookies.txt"
API_ENDPOINT="https://query1.finance.yahoo.com/v8/finance/chart/"
API_SUFFIX="?interval=1d"

# Check if NO_COLOR is set to disable colorization
if [ -z "$NO_COLOR" ]; then
  : "${COLOR_GREEN:=$'\e[32m'}"
  : "${COLOR_RED:=$'\e[31m'}"
  : "${COLOR_RESET:=$'\e[00m'}"
fi

SYMBOLS=()
DISPLAY_METALS=false

# Parse options
while getopts "g" opt; do
  case ${opt} in
    g )
      DISPLAY_METALS=true
      ;;
    * )
      echo "Usage: ./ticker.sh [-g] AAPL MSFT GOOG BTC-USD"
      exit 1
      ;;
  esac
done
shift $((OPTIND -1))

SYMBOLS+=("$@")

if ! $(type jq > /dev/null 2>&1); then
  echo "'jq' is not in the PATH. (See: https://stedolan.github.io/jq/)"
  exit 1
fi

if ! $(type bc > /dev/null 2>&1); then
  echo "'bc' is not in the PATH. (See: https://www.gnu.org/software/bc/)"
  exit 1
fi

if [ -z "$SYMBOLS" ] && [ "$DISPLAY_METALS" = false ]; then
  echo "Usage: ./ticker.sh [-g] AAPL MSFT GOOG BTC-USD"
  exit 1
fi

[ ! -d "$SESSION_DIR" ] && mkdir -m 700 "$SESSION_DIR"

preflight () {
  curl --silent --output /dev/null --cookie-jar "$COOKIE_FILE" "https://finance.yahoo.com" \
    -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}

fetch_chart () {
  local symbol=$1
  local url="${API_ENDPOINT}${symbol}${API_SUFFIX}"
  curl --silent -b "$COOKIE_FILE" "$url"
}

[ ! -f "$COOKIE_FILE" ] && preflight

fetch_metal_prices () {
  local gold_symbol="GC=F"
  local silver_symbol="SI=F"
  local platinum_symbol="PL=F"

  local gold_price=$(fetch_chart "$gold_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')
  local silver_price=$(fetch_chart "$silver_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')
  local platinum_price=$(fetch_chart "$platinum_symbol" | jq -r '.chart.result[0].meta.regularMarketPrice')

  local gold_silver_ratio=$(awk -v gold="$gold_price" -v silver="$silver_price" 'BEGIN {printf "%.2f", gold / silver}')

  # Define colors
  local COLOR_YELLOW=$'\e[33m'
  local COLOR_SILVER=$'\e[37m'
  local COLOR_LIGHT_GREY=$'\e[249m'
  local COLOR_BRIGHT_PURPLE=$'\e[35;1m'
  local COLOR_RESET=$'\e[0m'

  echo "Precious Metal Spot Prices:"
  echo "---------------------------"
  printf "${COLOR_YELLOW}Gold Spot:     $%.2f /OZ${COLOR_RESET}\n" "$gold_price"
  printf "${COLOR_SILVER}Silver Spot:   $%.2f /OZ${COLOR_RESET}\n" "$silver_price"
  printf "${COLOR_LIGHT_GREY}Platinum Spot: $%.2f /OZ${COLOR_RESET}\n" "$platinum_price"
  printf "${COLOR_BRIGHT_PURPLE}Gold/Silver Ratio: %.2f${COLOR_RESET}\n" "$gold_silver_ratio"
  echo ""
}

# Initialize an array to hold background process IDs
pids=()

if [ "$DISPLAY_METALS" = true ]; then
  fetch_metal_prices
fi

for symbol in "${SYMBOLS[@]}"; do
 (
  # Running in subshell 
  results=$(fetch_chart "$symbol")

  currentPrice=$(echo "$results" | jq -r '.chart.result[0].meta.regularMarketPrice')
  previousClose=$(echo "$results" | jq -r '.chart.result[0].meta.chartPreviousClose')
  currency=$(echo "$results" | jq -r '.chart.result[0].meta.currency')
  symbol=$(echo "$results" | jq -r '.chart.result[0].meta.symbol')

  [ "$previousClose" = "null" ] && previousClose="1.0"

  priceChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" 'BEGIN {printf "%.2f", currentPrice - previousClose}')
  percentChange=$(awk -v currentPrice="$currentPrice" -v previousClose="$previousClose" 'BEGIN {printf "%.2f", ((currentPrice - previousClose) / previousClose) * 100}')

  if (( $(echo "$priceChange >= 0" | bc -l) )); then
    color="$COLOR_GREEN"
  elif (( $(echo "$priceChange < 0" | bc -l) )); then
    color="$COLOR_RED"
  fi

  if [ -z "$NO_COLOR" ]; then
    printf "%s%-10s%8.2f%10.2f%8s%6.2f%%%s\n" \
      "$color" "$symbol" \
      "$currentPrice" "$priceChange" "$color" "$percentChange" \
      "$COLOR_RESET"
  else
    printf "%-10s%8.2f%10.2f%9.2f%%\n" \
      "$symbol" \
      "$currentPrice" "$priceChange" "$percentChange"
  fi 
 ) &

 # Stack PIDs
 pids+=($!)

done

# Wait for all background processes to finish
for pid in "${pids[@]}"; do
  wait "$pid"
done

