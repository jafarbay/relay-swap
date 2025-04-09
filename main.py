import aiohttp
import asyncio
from web3 import Web3
from eth_account import Account
import time
import random
import os

# ==== Конфигурация ==== 
config = {
    'rpcs': {
        130: "https://unichain-rpc.publicnode.com",     # UniChain 0x078d782b760474a361dda0af3839290b0ef57ad6
        34443 : "https://mode.drpc.org",           # Mode 0xd988097fb8612cc24eec14542bc03424c656005f
        57073 : "https://ink.drpc.org",          # Ink 0xf1815bd50389c46847f0bda824ec8da914045d14
        1135 : "https://lisk.drpc.org",          # Lisk 0x4200000000000000000000000000000000000006 (WETH)
        1868 : "https://soneium.drpc.org",          # Soneium 0xbA9986D2381edf1DA03B0B9c1f8b00dc4AacC369
        8453 : "https://base.drpc.org",          # Base 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
        10 : "https://optimism.drpc.org",          # Optimism 0x0b2c639c533813f4aa9d7837caf62653d097ff85
        # Добавляй нужные RPC-ссылки по Chain ID
    },
    'from_chain': 130,
    'to_chain': 130,
    'from_token_address': "0x0000000000000000000000000000000000000000",  # ETH
    'to_token_address': "0x078d782b760474a361dda0af3839290b0ef57ad6",    # USDC
    'slippage_tolerance': '1',
    'swap_back': True,
    'random_delay_range': (3, 7),
    'swap_cycles_range': (2, 4),  # Новое: диапазон количества циклов свапа
}

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

def get_web3(chain_id):
    rpc = config['rpcs'].get(chain_id)
    if not rpc:
        raise ValueError(f"❌ RPC не найден для chainId {chain_id}")
    return Web3(Web3.HTTPProvider(rpc))

def read_private_keys():
    try:
        with open('private_key.txt', 'r') as file:
            keys = [line.strip() for line in file.readlines() if line.strip()]
        if not keys:
            log("❌ Файл private_key.txt пуст", "red")
            return []
        return keys
    except FileNotFoundError:
        log("❌ Файл private_key.txt не найден", "red")
        log("📝 Создаю пустой файл private_key.txt", "yellow")
        with open('private_key.txt', 'w'): pass
        return []
    except Exception as e:
        log(f"❌ Ошибка: {e}", "red")
        return []

def get_balance(account, chain_id):
    web3 = get_web3(chain_id)
    balance = web3.eth.get_balance(account.address)
    return balance, web3.from_wei(balance, 'ether')

def get_token_balance(account, token_address, chain_id):
    web3 = get_web3(chain_id)
    token_address = Web3.to_checksum_address(token_address)
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
    return contract.functions.balanceOf(account.address).call()

def get_eth_to_swap(account):
    balance, _ = get_balance(account, config['from_chain'])
    portion = int(balance * 0.2)
    return portion, Web3.from_wei(portion, 'ether')

def get_usdc_to_swap(account):
    balance = get_token_balance(account, config['to_token_address'], config['to_chain'])
    return balance, balance / 10**6

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

async def send_transaction_bundle(account, quote_data):
    try:
        all_steps = quote_data['steps']
    except KeyError as e:
        log(f"❌ Неверная структура quote: {e}", "red")
        return False

    nonce_cache = {}
    step_idx = 0
    for step in all_steps:
        for idx, item in enumerate(step.get('items', [])):
            tx_data = item.get('data')
            if not tx_data:
                log(f"⚠️ Нет данных (step {step_idx}, item {idx})", "yellow")
                continue

            try:
                chain_id = int(tx_data['chainId'])
                web3 = get_web3(chain_id)
                if chain_id not in nonce_cache:
                    nonce_cache[chain_id] = web3.eth.get_transaction_count(account.address)

                tx = {
                    'from': account.address,
                    'to': Web3.to_checksum_address(tx_data['to']),
                    'value': int(tx_data['value']),
                    'data': tx_data['data'],
                    'chainId': chain_id,
                    'maxFeePerGas': int(tx_data['maxFeePerGas']),
                    'maxPriorityFeePerGas': int(tx_data['maxPriorityFeePerGas']),
                    'nonce': nonce_cache[chain_id],
                    'type': 2
                }

                gas_estimate = web3.eth.estimate_gas(tx)
                tx['gas'] = int(gas_estimate * 1.1)

                signed = web3.eth.account.sign_transaction(tx, account.key)
                tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)

                log(f"🚀 [{step_idx+1}.{idx+1}] Отправлено: {tx_hash.hex()}", "cyan")

                receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt['status'] == 1:
                    log(f"✅ Подтверждено: {tx_hash.hex()}", "green")
                else:
                    log(f"❌ Ошибка выполнения: {tx_hash.hex()}", "red")
                    return False

                nonce_cache[chain_id] += 1
                delay = random.randint(*config['random_delay_range'])
                log(f"⏳ Задержка: {delay} сек.", "yellow")
                time.sleep(delay)

            except Exception as e:
                log(f"❌ Ошибка транзакции: {e}", "red")
                return False

        step_idx += 1

    return True

async def process_account(private_key):
    try:
        account = Account.from_key(private_key)
        log(f"\n🕒 {time.strftime('%Y-%m-%d %H:%M:%S')} - {account.address}", "white")

        num_cycles = random.randint(*config['swap_cycles_range'])
        log(f"🔁 Выполняем {num_cycles} циклов свапов", "cyan")

        for cycle in range(1, num_cycles + 1):
            log(f"\n🔄 Цикл #{cycle}", "yellow")

            _, balance_eth = get_balance(account, config['from_chain'])
            if balance_eth < 0.00001:
                log(f"⚠️ Баланс ETH слишком низкий ({balance_eth} ETH)", "yellow")
                break

            # === ETH → USDC === 
            eth_amount, eth_view = get_eth_to_swap(account)
            if eth_amount < 10**13:
                log("⚠️ Недостаточно ETH для свапа", "yellow")
            else:
                log(f"🔁 ETH → USDC: {eth_view} ETH", "cyan")
                quote = await get_quote(account, config['from_token_address'], config['to_token_address'], eth_amount)
                if quote:
                    success = await send_transaction_bundle(account, quote)
                    if not success:
                        break
                else:
                    log("❌ Не удалось получить котировку ETH → USDC", "red")

            if config['swap_back']:
                time.sleep(10)

                # === USDC → ETH === 
                usdc_amount, usdc_view = get_usdc_to_swap(account)
                if usdc_amount < 10000:
                    log("⚠️ Недостаточно USDC для свапа", "yellow")
                else:
                    log(f"🔁 USDC → ETH: {usdc_view} USDC", "cyan")
                    quote = await get_quote(account, config['to_token_address'], config['from_token_address'], usdc_amount)
                    if quote:
                        success = await send_transaction_bundle(account, quote)
                        if not success:
                            break
                    else:
                        log("❌ Не удалось получить котировку USDC → ETH", "red")

            delay = random.randint(*config['random_delay_range'])
            log(f"⏳ Задержка между циклами: {delay} сек.", "yellow")
            time.sleep(delay)

    except Exception as e:
        log(f"❌ Ошибка аккаунта: {e}", "red")

async def main():
    log("🔒 Чтение ключей из private_key.txt...", "white")
    private_keys = read_private_keys()

    if not private_keys:
        log("❌ Нет ключей в private_key.txt", "red")
        return

    log(f"🔑 Загружено ключей: {len(private_keys)}", "green")

    for i, key in enumerate(private_keys):
        log(f"\n📊 Аккаунт {i+1}/{len(private_keys)}", "cyan")
        await process_account(key)

        if i < len(private_keys) - 1:
            delay = random.randint(5, 15)
            log(f"⏳ Пауза перед следующим аккаунтом: {delay} сек.", "yellow")
            time.sleep(delay)

    log("\n✅ Все аккаунты обработаны", "green")

if __name__ == "__main__":
    asyncio.run(main())
