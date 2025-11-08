# Testnet Trading Setup

This guide explains how to set up testnet trading with Binance and Gate.io to test your strategies with real API calls but fake money.

## Why Use Testnets?

Testnets let you:
- Make real API calls with actual network conditions and rate limits
- Test order placement, fills, and cancellations
- See real maker/taker fee structures
- Practice API integration before risking real money
- Get realistic performance data without financial risk

## Fee Structures

### Binance Testnet
- **Maker fees**: 0% (with BNB or VIP tier) to 0.1%
- **Taker fees**: 0.1% (standard tier)
- Using limit orders that sit on the book = maker (lower/no fees)
- Using market orders or aggressive limits = taker (higher fees)

### Gate.io Testnet
- **Maker fees**: 0.2%
- **Taker fees**: 0.2%

## Setup Instructions

### 1. Binance Testnet

**Create API Keys:**
1. Visit https://testnet.binance.vision/
2. Log in with GitHub or create an account
3. Generate API Key and Secret
4. Save them securely

**Configure Environment:**
```bash
export BINANCE_TESTNET_API_KEY="your_api_key_here"
export BINANCE_TESTNET_API_SECRET="your_api_secret_here"
```

Or add to your `.env` file:
```
BINANCE_TESTNET_API_KEY=your_api_key_here
BINANCE_TESTNET_API_SECRET=your_api_secret_here
```

**Get Testnet Funds:**
- Binance testnet provides free test BTC, ETH, USDT
- Use the faucet on the testnet site

### 2. Gate.io Testnet

**Create API Keys:**
1. Visit https://www.gate.io/testnet
2. Create an account or log in
3. Go to API Management
4. Create new API key with trading permissions
5. Save the API Key and Secret

**Configure Environment:**
```bash
export GATE_TESTNET_API_KEY="your_api_key_here"
export GATE_TESTNET_API_SECRET="your_api_secret_here"
```

Or add to your `.env` file:
```
GATE_TESTNET_API_KEY=your_api_key_here
GATE_TESTNET_API_SECRET=your_api_secret_here
```

## Usage in Code

### Paper Trading (Current Default)
```python
from app.execution import PaperExec

exec_client = PaperExec("bot_name")
# Simulated trading, no API calls
# Now includes realistic fee simulation (0% maker, 0.1% taker)
```

### Binance Testnet
```python
from app.execution import BinanceTestnetExec

exec_client = BinanceTestnetExec("bot_name")
# Real API calls to Binance testnet
# Real maker/taker fees
```

### Gate.io Testnet
```python
from app.execution import GateTestnetExec

exec_client = GateTestnetExec("bot_name")
# Real API calls to Gate.io testnet
# Real maker/taker fees
```

## Maker vs Taker Orders

The system now uses **limit orders by default** to maximize maker fills and minimize fees.

### How It Works

**Buy Orders:**
- Placed at 0.05% below current market price
- Sits on order book (maker)
- Waits up to 60 seconds for fill
- If filled as maker: 0% fee (Binance) or 0.2% fee (Gate.io)

**Sell Orders:**
- Placed at 0.05% above current market price
- Sits on order book (maker)
- Waits up to 60 seconds for fill
- If filled as maker: 0% fee (Binance) or 0.2% fee (Gate.io)

**Fallback:**
- If order doesn't fill within timeout, it's cancelled
- System can retry with market order (taker fees apply)

### Fee Impact Example

With 100 trades of $1000 each:
- **Taker fees (0.1%)**: $100 in fees
- **Maker fees (0%)**: $0 in fees
- **Savings**: $100 or 10% of single trade value

For active trading strategies, maker fees can make the difference between profitable and unprofitable.

## Monitoring Fees

Fee tracking is now built in:
- All trades record `fee` amount and `is_maker` flag
- Dashboard will show total fees paid
- Per-strategy fee analysis
- Maker/taker ratio tracking

## Next Steps

1. **Test with paper trading first** - verify strategies work
2. **Switch to testnet** - test real API integration
3. **Monitor fee impact** - see how maker/taker affects P&L
4. **Optimize for makers** - adjust timing and pricing
5. **Go live** - only after thorough testnet validation

## Troubleshooting

**"Missing credentials" error:**
- Verify environment variables are set correctly
- Check spelling of env var names
- Restart your application after setting env vars

**"Order failed" messages:**
- Check testnet has funds
- Verify API keys have trading permissions
- Check minimum order sizes (usually $10-100)
- Ensure symbol format is correct (BTC_USDT, ETH_USDT, etc.)

**Orders not filling:**
- Limit price may be too far from market
- Increase timeout or adjust price closer to market
- Check order book depth on testnet

## Security Notes

- **NEVER** commit API keys to git
- Use environment variables or .env files (add .env to .gitignore)
- Testnet keys are separate from production
- Even testnet keys should be kept private for security practice
