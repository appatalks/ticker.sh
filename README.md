> [!NOTE]
> The original [ticker.sh](https://github.com/pstadler/ticker.sh) has been discontinued. This fork aims to help keep similar functionality alive.

----

# ticker.sh

`ticker.sh` is a Bash script that retrieves and displays live stock prices, along with precious metal prices (gold, silver, platinum) and the gold-to-silver ratio. It uses Yahoo Finance as the source for fetching data and can display results in color-coded output for better readability.

![ticker.sh](https://raw.githubusercontent.com/appatalks/ticker.sh/main/screenshot.png)

## Features

- Fetch live stock prices by providing stock symbols (e.g., AAPL, MSFT, GOOG).
- Display live spot prices for gold, silver, and platinum.
- Calculate and display the Gold/Silver ratio.
- Color-coded output for price changes and metal prices.
- Option to disable colorization by setting the `NO_COLOR` environment variable.

## Prerequisites

Ensure the following dependencies are installed:

- [curl](https://curl.se/)
- [jq](https://stedolan.github.io/jq/) - for JSON parsing.
- [bc](https://www.gnu.org/software/bc/) - for arithmetic operations.

## Installation

1. Clone the repository or download the `ticker.sh` script.
2. Ensure the script is executable:

   ```bash
   chmod +x ticker.sh
   ```
3. Install the required dependencies:

   ```bash
   sudo apt-get install jq bc curl   # For Debian-based systems
   ```

## Usage

### Fetch Stock Prices

You can fetch live stock prices by passing the stock symbols as arguments in the order that you would like to see. For example, to retrieve the prices for Apple (AAPL), Microsoft (MSFT), and Google (GOOG), use the following command:

    ./ticker.sh AAPL MSFT GOOG BTC-USD

This will display the current price, the price change, and the percentage change.

### Fetch Precious Metal Prices

To fetch the spot prices for gold, silver, platinum, and the gold-to-silver ratio, use the `-g` flag:

    ./ticker.sh -g

### Fetch Both Stock and Precious Metal Prices

You can also fetch both stock prices and precious metal prices in a single command:

    ./ticker.sh -g AAPL MSFT GOOG BTC-USD

This will display both the spot prices for the metals and the prices for the given stock symbols.

### Sort Stock Prices by ```gain/loss```

You can also sort the stock prices by ```gain/loss``` percentage with the ```-s``` flag:

    ./ticker.sh -s AAPL MSFT GOOG BTC-USD

This will sort the stock prices for the given stock symbols.

### Disable Color Output

If you are running the script in an environment that doesn't support color or if you prefer plain text output, you can disable colorization by setting the `NO_COLOR` environment variable:

    NO_COLOR=1 ./ticker.sh AAPL MSFT GOOG BTC-USD

### AI Alerts (Optional - with ai_alert.py)

This fork adds an optional AI-powered alerting feature that analyzes recent price indicators and returns a compact recommendation. Take advice with CAUTION.

- Trigger an alert for each symbol with a timeframe using `-a TIMEFRAME` or `--alert TIMEFRAME` (e.g. `1m`, `5m`, `1h`, `1d`).
- Include `-r` or `--rationale` to print a short rationale after the recommendation.
- Use `-d` or `--debug` to print the helper payload preview and raw model response for debugging.

Examples:

        # Request AI alert (compact recommendation)
        ./ticker.sh -a 5m RXT

        # Include rationale and debug info
        ./ticker.sh -a 5m -r -d RXT

Environment and configuration

- Place your OpenAI API key in a `.env` file or export `OPENAI_API_KEY`.
- Optional environment variables:
    - `OPENAI_MODEL` (default: `gpt-5-mini`)
    - `OPENAI_MAX_COMPLETION_TOKENS` (default: `650`)
    - `OPENAI_USE_RESPONSES` (set to `1` to use the Responses API - recommended; default `0`)

Notes

- The helper script `ai_alert.py` performs the indicator calculations (RSI, MACD, PPO) locally and sends a deterministic prompt to the OpenAI API. When no API key is present the helper returns a simple RSI-based heuristic (no network call).
- The script rate-limits AI calls (configurable via `ALERT_DELAY`) when processing multiple symbols to avoid overage.

AI output clarification
-----------------------

When the script prints an AI recommendation inline it may include a status marker and a confidence score. The markers indicate whether the helper contacted the model:

- [S] — AI helper called the model successfully and returned a recommendation.
- [F] — AI helper failed to call the model (network or API error) and used local fallback heuristics to produce a recommendation.

Confidence score:

- Recommendations include an integer confidence score from 1 to 10 (10 = highest confidence). Use the score to weigh the recommendation.


### PRO TIP

> [!NOTE]
> Use a foreloop for continious ```5 minute``` monitoring and AI rationale:
>
> ```bash
> while true; do ./ticker.sh -gs -r 5m SPY B HNST MSFT PFE PLG PYPL RXT WEAT; sleep 300; clear; done
> ``` 

## License

This script is provided as-is under the ```MIT License```. You are free to modify and distribute it under the terms of the license.

## Contributions

Contributions are welcome! If you encounter bugs or have feature suggestions, feel free to submit an issue or pull request.

---

### Author

Created by ```@pstadler``` <br> 
Updated by ```@appatalks```

