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

You can fetch live stock prices by passing the stock symbols as arguments. For example, to retrieve the prices for Apple (AAPL), Microsoft (MSFT), and Google (GOOG), use the following command:

    ./ticker.sh AAPL MSFT GOOG BTC-USD

This will display the current price, the price change, and the percentage change.

### Fetch Precious Metal Prices

To fetch the spot prices for gold, silver, platinum, and the gold-to-silver ratio, use the `-g` flag:

    ./ticker.sh -g

### Fetch Both Stock and Precious Metal Prices

You can also fetch both stock prices and precious metal prices in a single command:

    ./ticker.sh -g AAPL MSFT GOOG BTC-USD

This will display both the spot prices for the metals and the prices for the given stock symbols.

### Disable Color Output

If you are running the script in an environment that doesn't support color or if you prefer plain text output, you can disable colorization by setting the `NO_COLOR` environment variable:

    NO_COLOR=1 ./ticker.sh AAPL MSFT GOOG BTC-USD

### PRO TIP

> [!NOTE]
> Use a foreloop for continious ```5 minute``` monitoring:
>
> ```bash
> while true; do ./ticker.sh -g SPY GOLD HNST MSFT PFE PLG PYPL RXT WEAT; sleep 300; clear; done
> ``` 

## License

This script is provided as-is under the ```MIT License```. You are free to modify and distribute it under the terms of the license.

## Contributions

Contributions are welcome! If you encounter bugs or have feature suggestions, feel free to submit an issue or pull request.

---

### Author

Created by ```@pstadler``` <br> 
Updated by ```@appatalks```

