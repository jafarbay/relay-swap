import aiohttp
import asyncio
from web3 import Web3
from eth_account import Account
import time
import random

# ==== Конфигурация ==== 
config = {
    'rpc_node': "https://unichain-rpc.publicnode.com",  # RPC UniChain (Chain ID 130)
    'private_key': "",  # 🔑 Укажи приватный ключ
    'from_chain': 130,  # Исходный Chain ID
    'to_chain': 130,  # Целевой Chain ID
    'from_token_address': "0x0000000000000000000000000000000000000000",  # Адрес токена исходной цепи (ETH, например)
    'to_token_address': "0x078d782b760474a361dda0af3839290b0ef57ad6",  # Адрес токена целевой цепи (например, USDC)
    'slippage_tolerance': '1',  # Допустимое проскальзывание
    'swap_back': True,  # Если True, будет происходить обмен обратно (USDC → ETH)
    'random_delay_range': (3, 7),  # Диапазон случайной задержки между транзакциями (в секундах)
}

# ==== Инициализация ==== 
web3 = Web3(Web3.HTTPProvider(config['rpc_node']))
account = Account.from_key(config['private_key'])

# ==== Цветные логи ==== 
def log(msg, color="white"):
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m"
    }
    print(f"{colors.get(color, colors['white'])}{msg}{colors['reset']}")

# ==== Получение баланса эфира ==== 
def get_balance(account):
    balance = web3.eth.get_balance(account.address)
    return balance, web3.from_wei(balance, 'ether')

# ==== Получение баланса токена ==== 
def get_token_balance(account, token_address):
    token_address = Web3.to_checksum_address(token_address)  # Преобразуем адрес в чексума
    abi = [{
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }]
    contract = web3.eth.contract(address=token_address, abi=abi)
    balance = contract.functions.balanceOf(account.address).call()
    return balance  # в 6 знаках


# ==== Расчёт доли баланса ==== 
def get_eth_to_swap(account):
    balance, _ = get_balance(account)
    portion = int(balance * 0.2)
    return portion, web3.from_wei(portion, 'ether')

def get_usdc_to_swap(account):
    balance = get_token_balance(account, config['to_token_address'])
    portion = int(balance)
    return portion, portion / 10**6  # в "нормальном" формате

# ==== Получение котировки ==== 
async def get_quote(account, from_token, to_token, amount):
    url = "https://api.relay.link/quote"
    headers = {
        'accept': 'application/json, text/plain, */*',
        'user-agent': 'Mozilla/5.0'
    }
    json_data = {
        'user': account.address.lower(),
        'originChainId': config['from_chain'],
        'destinationChainId': config['to_chain'],
        'originCurrency': from_token,
        'destinationCurrency': to_token,
        'recipient': account.address,
        'tradeType': 'EXACT_INPUT',
        'amount': str(amount),
        'referrer': 'relay.link/swap',
        'slippageTolerance': config['slippage_tolerance'],
        'useExternalLiquidity': False,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=json_data) as response:
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                log(f"❌ Ошибка API: {text}", "red")
                return None

# ==== Отправка всех транзакций из quote (в правильном порядке) ==== 
async def send_transaction_bundle(account, quote_data):
    try:
        all_steps = quote_data['steps']
    except KeyError as e:
        log(f"❌ Ошибка в структуре quote: {e}", "red")
        return False

    nonce = web3.eth.get_transaction_count(account.address)
    step_idx = 0

    for step in all_steps:
        step_items = step.get('items', [])
        for idx, item in enumerate(step_items):
            tx_data = item.get('data')
            if not tx_data:
                log(f"⚠️ Нет данных для транзакции (step {step_idx}, item {idx})", "yellow")
                continue

            try:
                tx = {
                    'from': account.address,
                    'to': Web3.to_checksum_address(tx_data['to']),
                    'value': int(tx_data['value']),
                    'data': tx_data['data'],
                    'chainId': int(tx_data['chainId']),
                    'maxFeePerGas': int(tx_data['maxFeePerGas']),
                    'maxPriorityFeePerGas': int(tx_data['maxPriorityFeePerGas']),
                    'nonce': nonce,
                    'type': 2
                }

                gas_estimate = web3.eth.estimate_gas(tx)
                tx['gas'] = int(gas_estimate * 1.1)

                signed = web3.eth.account.sign_transaction(tx, config['private_key'])
                tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)

                log(f"🚀 [{step_idx+1}.{idx+1}] Отправлено: {tx_hash.hex()}", "cyan")

                receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt['status'] == 1:
                    log(f"✅ Подтверждено: {tx_hash.hex()}", "green")
                else:
                    log(f"❌ Ошибка выполнения: {tx_hash.hex()}", "red")
                    return False

                nonce += 1

                # Случайная задержка между транзакциями
                delay = random.randint(config['random_delay_range'][0], config['random_delay_range'][1])
                log(f"⏳ Задержка: {delay} сек.", "yellow")
                time.sleep(delay)

            except Exception as e:
                log(f"❌ Ошибка отправки транзакции: {e}", "red")
                return False

        step_idx += 1

    return True

# ==== Основной процесс ==== 
async def main():
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log(f"🕒 {current_time} - Старт для адреса: {account.address}", "white")

    balance_wei, balance_eth = get_balance(account)
    if balance_eth < 0.00001:
        log(f"⚠️ Баланс ETH слишком низкий ({balance_eth} ETH)", "yellow")
        return

    # === ETH → USDC === 
    eth_amount, eth_view = get_eth_to_swap(account)
    if eth_amount < 10**13:  # меньше 0.00001 ETH
        log("⚠️ Недостаточно ETH для свапа", "yellow")
    else:
        log(f"🔁 ETH → USDC: {eth_view} ETH", "cyan")
        quote = await get_quote(account, config['from_token_address'], config['to_token_address'], eth_amount)
        if quote:
            await send_transaction_bundle(account, quote)
        else:
            log("❌ Не удалось получить котировку ETH → USDC", "red")

    if config['swap_back']:  # Если включен флаг обмена обратно
        time.sleep(10)  # Задержка перед обменом обратно

        # === USDC → ETH === 
        usdc_amount, usdc_view = get_usdc_to_swap(account)
        if usdc_amount < 10000:  # меньше 0.01 USDC
            log("⚠️ Недостаточно USDC для свапа", "yellow")
        else:
            log(f"🔁 USDC → ETH: {usdc_view} USDC", "cyan")
            quote = await get_quote(account, config['to_token_address'], config['from_token_address'], usdc_amount)
            if quote:
                await send_transaction_bundle(account, quote)
            else:
                log("❌ Не удалось получить котировку USDC → ETH", "red")

# ==== Запуск ==== 
if __name__ == "__main__":
    asyncio.run(main())
