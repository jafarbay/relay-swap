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
    },
    'from_chain': 10,
    'to_chain': 10,
    'from_token_address': "0x0000000000000000000000000000000000000000",  # ETH
    'to_token_address': "0x0b2c639c533813f4aa9d7837caf62653d097ff85",    # USDC
    'slippage_tolerance': '1',
    'swap_back': True,
    'random_delay_range': (15, 60),
    'swap_cycles_range': (10, 15),
    'max_retries': 3,
    'retry_delay': (5, 10),
    'default_gas': {
        'approve': 100000,
        'swap': 300000
    }
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
        with open('private_key.txt', 'w'): pass
        return []
    except Exception as e:
        log(f"❌ Ошибка: {e}", "red")
        return []

async def get_current_nonce(web3, address):
    """Безопасное получение nonce с повторными попытками"""
    for _ in range(3):
        try:
            return web3.eth.get_transaction_count(address)
        except Exception as e:
            log(f"⚠️ Ошибка получения nonce: {e}", "yellow")
            await asyncio.sleep(1)
    raise Exception("Не удалось получить nonce")

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
        try:
            async with session.post(url, headers=headers, json=json_data, timeout=30) as response:
                if response.status == 200:
                    return await response.json()
                text = await response.text()
                log(f"❌ Ошибка API ({response.status}): {text[:200]}", "red")
                return None
        except Exception as e:
            log(f"❌ Ошибка запроса котировки: {str(e)[:200]}", "red")
            return None

async def send_transaction_with_retry(web3, tx, account, max_retries=3):
    """Отправка транзакции с обработкой ошибок nonce"""
    for attempt in range(max_retries):
        try:
            # Обновляем nonce перед каждой попыткой
            tx['nonce'] = await get_current_nonce(web3, account.address)
            
            # Подписываем и отправляем транзакцию
            signed_tx = account.sign_transaction(tx)
            tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
            return tx_hash
        except ValueError as e:
            if 'nonce too low' in str(e):
                log(f"⚠️ Nonce слишком низкий, попытка {attempt + 1}/{max_retries}", "yellow")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise
            raise
        except Exception as e:
            log(f"❌ Ошибка отправки транзакции: {str(e)[:200]}", "red")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            raise
    raise Exception("Не удалось отправить транзакцию после нескольких попыток")

async def send_transaction_bundle(account, quote_data):
    max_retries = config['max_retries']
    try:
        all_steps = quote_data['steps']
    except KeyError as e:
        log(f"❌ Неверная структура quote: {e}", "red")
        return False

    nonce_cache = {}
    
    for step_idx, step in enumerate(all_steps):
        step_id = step.get('id', '').lower()
        step_kind = step.get('kind', '').lower()
        
        if step_kind != 'transaction':
            log(f"⚠️ Пропускаем шаг {step_idx+1} (не транзакция): {step_id}", "yellow")
            continue
            
        log(f"🔹 Обрабатываем шаг {step_idx+1}: {step.get('description', '')}", "cyan")
        
        for idx, item in enumerate(step.get('items', [])):
            tx_data = item.get('data')
            if not tx_data:
                log(f"⚠️ Нет данных (step {step_idx+1}, item {idx+1})", "yellow")
                continue

            for attempt in range(max_retries):
                try:
                    chain_id = int(tx_data['chainId'])
                    web3 = get_web3(chain_id)
                    
                    # Получаем актуальный nonce
                    current_nonce = await get_current_nonce(web3, account.address)
                    if chain_id in nonce_cache:
                        nonce_cache[chain_id] = max(nonce_cache[chain_id], current_nonce)
                    else:
                        nonce_cache[chain_id] = current_nonce

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

                    if 'approve' in step_id:
                        log(f"🔐 Обнаружен approve токена (попытка {attempt+1}/{max_retries})", "yellow")
                        tx['gas'] = config['default_gas']['approve']
                    else:
                        try:
                            gas_estimate = web3.eth.estimate_gas(tx)
                            tx['gas'] = int(gas_estimate * 1.3)
                            log(f"🔁 Обнаружен swap (попытка {attempt+1}/{max_retries})", "yellow")
                        except Exception as e:
                            log(f"⚠️ Ошибка оценки газа: {e}", "yellow")
                            tx['gas'] = config['default_gas']['swap']

                    # Отправка транзакции с обработкой nonce
                    tx_hash = await send_transaction_with_retry(web3, tx, account)
                    log(f"🚀 [{step_idx+1}.{idx+1}] Отправлено: {tx_hash.hex()}", "cyan")

                    # Ожидаем подтверждения
                    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                    if receipt['status'] == 1:
                        log(f"✅ Подтверждено: {tx_hash.hex()}", "green")
                        nonce_cache[chain_id] += 1
                        break
                    else:
                        log(f"❌ Ошибка выполнения: {tx_hash.hex()}", "red")
                        if attempt < max_retries - 1:
                            delay = random.randint(*config['retry_delay'])
                            log(f"⏳ Повтор через {delay} сек...", "yellow")
                            await asyncio.sleep(delay)
                            continue
                        return False

                    delay = random.randint(*config['random_delay_range'])
                    log(f"⏳ Задержка: {delay} сек.", "yellow")
                    await asyncio.sleep(delay)

                except Exception as e:
                    log(f"❌ Ошибка транзакции (попытка {attempt+1}): {str(e)[:200]}", "red")
                    if 'nonce too low' in str(e):
                        nonce_cache[chain_id] = await get_current_nonce(web3, account.address)
                    if attempt < max_retries - 1:
                        delay = random.randint(*config['retry_delay'])
                        log(f"⏳ Повтор через {delay} сек...", "yellow")
                        await asyncio.sleep(delay)
                        continue
                    return False

    return True

async def process_swap(account, from_token, to_token, get_amount_func, token_from_name, token_to_name):
    amount, amount_view = get_amount_func(account)
    if amount < (10**13 if from_token == config['from_token_address'] else 10000):
        log(f"⚠️ Недостаточно {token_from_name} для свапа", "yellow")
        return True

    for attempt in range(config['max_retries']):
        log(f"🔁 {token_from_name} → {token_to_name}: {amount_view} {token_from_name} (попытка {attempt+1})", "cyan")
        quote = await get_quote(account, from_token, to_token, amount)
        
        if not quote:
            if attempt < config['max_retries'] - 1:
                delay = random.randint(*config['retry_delay'])
                log(f"⏳ Повторный запрос котировки через {delay} сек...", "yellow")
                await asyncio.sleep(delay)
                continue
            log(f"❌ Не удалось получить котировку {token_from_name} → {token_to_name}", "red")
            return False

        success = await send_transaction_bundle(account, quote)
        if success:
            return True
        elif attempt < config['max_retries'] - 1:
            delay = random.randint(*config['retry_delay'])
            log(f"⏳ Повторный свап через {delay} сек...", "yellow")
            await asyncio.sleep(delay)
            continue
    
    log(f"❌ Превышено количество попыток свапа {token_from_name} → {token_to_name}", "red")
    return False

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

            # ETH → USDC
            if not await process_swap(
                account,
                config['from_token_address'],
                config['to_token_address'],
                get_eth_to_swap,
                "ETH",
                "USDC"
            ):
                break

            if config['swap_back']:
                await asyncio.sleep(10)
                
                # USDC → ETH
                if not await process_swap(
                    account,
                    config['to_token_address'],
                    config['from_token_address'],
                    get_usdc_to_swap,
                    "USDC",
                    "ETH"
                ):
                    break

            delay = random.randint(*config['random_delay_range'])
            log(f"⏳ Задержка между циклами: {delay} сек.", "yellow")
            await asyncio.sleep(delay)

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
            await asyncio.sleep(delay)

    log("\n✅ Все аккаунты обработаны", "green")

if __name__ == "__main__":
    asyncio.run(main())
