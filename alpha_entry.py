import time
import decimal
import requests
import sys
import logging
from apexpro.constants import APEX_OMNI_HTTP_MAIN, NETWORKID_OMNI_MAIN_ARB
from apexpro.http_private_sign import HttpPrivateSign
from apexpro.http_public import HttpPublic
from clients import private_client, public_client

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Set your API credentials (replace with your actual credentials)


# Wrapper function to execute API calls with retries
def api_call_with_retry(api_func, *args, **kwargs):
    max_retries = 5
    backoff_factor = 0.5
    for attempt in range(max_retries):
        try:
            response = api_func(*args, **kwargs)
            if response is None:
                raise ValueError("Empty response received")
            return response
        except (requests.exceptions.RequestException, ValueError) as e:
            logging.warning(f"API call failed ({e}), retrying {attempt + 1}/{max_retries} ...")
            time.sleep(backoff_factor * (2 ** attempt))
    raise Exception(f"Failed to execute API call after {max_retries} attempts")

# Fetch account data
def fetch_account_data():
    account_balance_res = api_call_with_retry(private_client.get_account_balance_v3)
    return account_balance_res

# Fetch current price of the symbol
def fetch_current_price(symbol):
    ticker = api_call_with_retry(public_client.ticker_v3, symbol=symbol)
    return decimal.Decimal(ticker['data'][0]['lastPrice'])

# Calculate position size
def calculate_position_size(account_data, leverage, current_price, step_size):
    available_margin = decimal.Decimal(account_data['data']['availableBalance'])
    real_leverage = leverage * 100  # Convert leverage to a percentage value
    position_size = (available_margin * real_leverage) / current_price
    # Round the position size to the nearest multiple of step_size
    position_size = (position_size // step_size) * step_size
    return position_size

# Fetch tick size and step size for symbol
def fetch_tick_and_step_size(symbol):
    response = api_call_with_retry(private_client.configs_v3)
    if 'data' not in response:
        raise Exception(f"Error fetching configs: {response}")

    contract_config = response.get('data', {}).get('contractConfig', {})
    perpetual_contracts = contract_config.get('perpetualContract', [])

    if not perpetual_contracts:
        raise Exception("No perpetual contracts found under contractConfig.")
    
    symbol_data = next((item for item in perpetual_contracts if item['symbol'] == symbol), None)
    if not symbol_data:
        raise Exception(f"Symbol data not found for {symbol}")
    
    tick_size = decimal.Decimal(symbol_data['tickSize'])
    step_size = decimal.Decimal(symbol_data['stepSize'])
    return tick_size, step_size

def place_optimal_limit_order(symbol, direction, leverage, tp_percent, sl_percent):
    accountData = private_client.get_account_v3()
    account_data = fetch_account_data()
    tick_size, step_size = fetch_tick_and_step_size(symbol)
    current_price = fetch_current_price(symbol)
    position_size = calculate_position_size(account_data, leverage, current_price, step_size)

    if direction.lower() == "buy":
        tp_price = current_price * (1 + decimal.Decimal(tp_percent) / 100)
        sl_price = current_price * (1 - decimal.Decimal(sl_percent) / 100)
    elif direction.lower() == "sell":
        tp_price = current_price * (1 - decimal.Decimal(tp_percent) / 100)
        sl_price = current_price * (1 + decimal.Decimal(sl_percent) / 100)
    else:
        raise ValueError("Direction must be either 'buy' or 'sell'")

    tp_price = (tp_price // tick_size) * tick_size
    sl_price = (sl_price // tick_size) * tick_size

    # Place initial market order
    initial_order = api_call_with_retry(private_client.create_order_v3,
                                        symbol=symbol,
                                        side=direction.upper(),
                                        type="MARKET",
                                        size=str(position_size),
                                        price=str(current_price),
                                        timeInForce="GOOD_TIL_CANCEL",
                                        timestampSeconds=int(time.time()))
    logging.info(f"Initial Order: {initial_order}")

    # Place Stop Loss order
    stop_order = api_call_with_retry(private_client.create_order_v3,
                                     symbol=symbol,
                                     side="SELL" if direction.lower() == "buy" else "BUY",
                                     isPositionTpsl=True,
                                     reduceOnly=True,
                                     type="STOP_MARKET",
                                     size=str(position_size),
                                     timestampSeconds=int(time.time()),
                                     price=str(sl_price),
                                     timeInForce="GOOD_TIL_CANCEL",
                                     triggerPriceType="INDEX",
                                     triggerPrice=str(sl_price))
    logging.info(f"Stop Loss Order: {stop_order}")

    # Place Take Profit order
    tp_order = api_call_with_retry(private_client.create_order_v3,
                                   symbol=symbol,
                                   side="SELL" if direction.lower() == "buy" else "BUY",
                                   isPositionTpsl=True,
                                   reduceOnly=True,
                                   type="TAKE_PROFIT_MARKET",
                                   size=str(position_size),
                                   timestampSeconds=int(time.time()),
                                   price=str(tp_price),
                                   timeInForce="GOOD_TIL_CANCEL",
                                   triggerPriceType="INDEX",
                                   triggerPrice=str(tp_price))
    logging.info(f"Take Profit Order: {tp_order}")

if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Usage: python buy_btc.py <symbol> <direction> <leverage> <tp_percent> <sl_percent>")
        sys.exit(1)

    symbol = sys.argv[1]
    direction = sys.argv[2]
    leverage = decimal.Decimal(sys.argv[3])
    tp_percent = decimal.Decimal(sys.argv[4])
    sl_percent = decimal.Decimal(sys.argv[5])

    logging.info(f"Arguments received: symbol={symbol}, direction={direction}, leverage={leverage}, tp_percent={tp_percent}, sl_percent={sl_percent}")

    try:
        place_optimal_limit_order(symbol, direction, leverage, tp_percent, sl_percent)
    except Exception as e:
        logging.error(f"Error placing order: {e}")