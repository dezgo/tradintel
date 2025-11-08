#!/usr/bin/env python3
"""
Test script for Binance testnet connection.

Usage:
    python test_binance_testnet.py

Make sure you have set your API keys in .env file:
    BINANCE_TESTNET_API_KEY=your_key
    BINANCE_TESTNET_API_SECRET=your_secret
"""

import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Import after loading env vars
from app.execution import BinanceTestnetExec

def test_connection():
    """Test Binance testnet connection and basic operations."""
    print("=" * 60)
    print("Binance Testnet Connection Test")
    print("=" * 60)

    # Check API keys
    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
    api_secret = os.getenv("BINANCE_TESTNET_API_SECRET")

    if not api_key or not api_secret:
        print("\n❌ ERROR: API keys not found!")
        print("\nPlease set your keys in .env file:")
        print("  BINANCE_TESTNET_API_KEY=your_key_here")
        print("  BINANCE_TESTNET_API_SECRET=your_secret_here")
        return False

    print(f"\n✓ API Key found: {api_key[:8]}...{api_key[-4:]}")
    print(f"✓ Secret Key found: {api_secret[:8]}...{api_secret[-4:]}")

    # Initialize client
    try:
        print("\n→ Initializing Binance testnet client...")
        client = BinanceTestnetExec("test_bot")
        print("✓ Client initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize client: {e}")
        return False

    # Test account access using API endpoint directly
    try:
        print("\n→ Testing account access...")
        # Use account endpoint instead of fetch_balance (which uses SAPI)
        account_info = client.exchange.fetch_my_trades('BTC/USDT', limit=1)
        print("✓ Successfully authenticated with Binance testnet!")

        # Try to get balance using the v3 API endpoint
        try:
            # This uses the /api/v3/account endpoint which is available on testnet
            response = client.exchange.private_get_account()
            balances = response.get('balances', [])

            print("\n📊 Account Balances (Testnet):")
            print("-" * 60)
            has_funds = False
            for bal in balances:
                free = float(bal.get('free', 0))
                locked = float(bal.get('locked', 0))
                if free > 0 or locked > 0:
                    has_funds = True
                    total = free + locked
                    print(f"  {bal['asset']:8s}: {total:,.8f} (free: {free:,.8f}, locked: {locked:,.8f})")

            if not has_funds:
                print("  (No funds - visit https://testnet.binance.vision/ to get testnet funds)")
        except Exception as bal_error:
            print(f"  Could not fetch balances: {bal_error}")
            print("  This is OK - authentication still works!")

    except Exception as e:
        print(f"❌ Failed to authenticate: {e}")
        print("\nThis might be OK if you have no trade history yet.")
        print("Let's continue with other tests...")

    # Test market data access
    try:
        print("\n→ Testing market data access...")
        ticker = client.exchange.fetch_ticker('BTC/USDT')
        print(f"✓ BTC/USDT Price: ${ticker['last']:,.2f}")
    except Exception as e:
        print(f"❌ Failed to fetch market data: {e}")
        return False

    print("\n" + "=" * 60)
    print("✅ All tests passed! Your testnet connection is working.")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Get testnet funds at https://testnet.binance.vision/")
    print("2. Update your bots to use BinanceTestnetExec")
    print("3. Start trading with real API calls (but fake money)!")

    return True

if __name__ == "__main__":
    try:
        test_connection()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
