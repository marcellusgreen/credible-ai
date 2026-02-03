#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-End Stripe Checkout Flow Test

This script tests the complete Stripe checkout flow:
1. Creates checkout sessions for Pro/Business subscriptions
2. Creates checkout sessions for credit packages
3. Verifies checkout URLs are valid Stripe URLs
4. Tests webhook signature verification
5. Simulates webhook events (in test mode)

Usage:
    python scripts/test_stripe_checkout_e2e.py
    python scripts/test_stripe_checkout_e2e.py --live  # Use live API (careful!)
    python scripts/test_stripe_checkout_e2e.py --verbose

Requirements:
    - STRIPE_API_KEY (test mode recommended: sk_test_...)
    - STRIPE_WEBHOOK_SECRET (whsec_...)
    - STRIPE_PRO_PRICE_ID, STRIPE_BUSINESS_PRICE_ID
    - STRIPE_CREDITS_10_PRICE_ID, etc.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import stripe


class StripeCheckoutTester:
    """End-to-end tester for Stripe checkout flow."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results = []

        # Initialize Stripe
        self.stripe_key = os.getenv("STRIPE_API_KEY")
        if not self.stripe_key:
            raise ValueError("STRIPE_API_KEY environment variable not set")

        stripe.api_key = self.stripe_key

        # Check if we're in test mode
        self.is_test_mode = self.stripe_key.startswith("sk_test_")
        if not self.is_test_mode:
            print("\n** WARNING: Using LIVE Stripe API key! **")
            print("   This will create real charges. Use sk_test_* for testing.\n")

        # Load price IDs
        self.pro_price_id = os.getenv("STRIPE_PRO_PRICE_ID")
        self.business_price_id = os.getenv("STRIPE_BUSINESS_PRICE_ID")
        self.credit_price_ids = {
            10: os.getenv("STRIPE_CREDITS_10_PRICE_ID"),
            25: os.getenv("STRIPE_CREDITS_25_PRICE_ID"),
            50: os.getenv("STRIPE_CREDITS_50_PRICE_ID"),
            100: os.getenv("STRIPE_CREDITS_100_PRICE_ID"),
        }
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    def log(self, message: str):
        """Log message if verbose mode."""
        if self.verbose:
            print(f"  → {message}")

    def log_result(self, test_name: str, passed: bool, message: str, details: str = None):
        """Log a test result."""
        if passed:
            status = "[PASS]"
            self.passed += 1
        else:
            status = "[FAIL]"
            self.failed += 1

        print(f"  {status} {test_name}: {message}")
        if details and self.verbose:
            print(f"         {details}")

        self.results.append({
            "test": test_name,
            "passed": passed,
            "message": message,
            "details": details,
        })

    def log_skip(self, test_name: str, reason: str):
        """Log a skipped test."""
        print(f"  [SKIP] {test_name}: {reason}")
        self.skipped += 1
        self.results.append({
            "test": test_name,
            "passed": None,
            "message": f"Skipped: {reason}",
        })

    # =========================================================================
    # Test: Stripe Connection
    # =========================================================================

    def test_stripe_connection(self):
        """Test that we can connect to Stripe API."""
        print("\n[1/8] Testing Stripe API Connection...")

        try:
            account = stripe.Account.retrieve()
            self.log_result(
                "Stripe connection",
                True,
                f"Connected to account: {account.get('business_profile', {}).get('name', account.id)}",
                f"Mode: {'TEST' if self.is_test_mode else 'LIVE'}"
            )
            return True
        except stripe.error.AuthenticationError as e:
            self.log_result("Stripe connection", False, f"Authentication failed: {e}")
            return False
        except Exception as e:
            self.log_result("Stripe connection", False, f"Error: {e}")
            return False

    # =========================================================================
    # Test: Price IDs Exist
    # =========================================================================

    def test_price_ids_exist(self):
        """Test that configured price IDs exist in Stripe."""
        print("\n[2/8] Testing Price IDs...")

        # Pro price
        if self.pro_price_id and not self.pro_price_id.startswith("price_pro"):
            try:
                price = stripe.Price.retrieve(self.pro_price_id)
                self.log_result(
                    "Pro price ID",
                    price.active,
                    f"${price.unit_amount / 100:.2f}/{price.recurring.interval}" if price.recurring else "One-time",
                    f"ID: {self.pro_price_id}"
                )
            except stripe.error.InvalidRequestError:
                self.log_result("Pro price ID", False, f"Price not found: {self.pro_price_id}")
        else:
            self.log_skip("Pro price ID", "Using placeholder ID")

        # Business price
        if self.business_price_id and not self.business_price_id.startswith("price_business"):
            try:
                price = stripe.Price.retrieve(self.business_price_id)
                self.log_result(
                    "Business price ID",
                    price.active,
                    f"${price.unit_amount / 100:.2f}/{price.recurring.interval}" if price.recurring else "One-time",
                    f"ID: {self.business_price_id}"
                )
            except stripe.error.InvalidRequestError:
                self.log_result("Business price ID", False, f"Price not found: {self.business_price_id}")
        else:
            self.log_skip("Business price ID", "Using placeholder ID")

        # Credit packages
        for amount, price_id in self.credit_price_ids.items():
            if price_id and not price_id.startswith("price_credits"):
                try:
                    price = stripe.Price.retrieve(price_id)
                    self.log_result(
                        f"Credits ${amount} price ID",
                        price.active,
                        f"${price.unit_amount / 100:.2f} one-time",
                        f"ID: {price_id}"
                    )
                except stripe.error.InvalidRequestError:
                    self.log_result(f"Credits ${amount} price ID", False, f"Price not found: {price_id}")
            else:
                self.log_skip(f"Credits ${amount} price ID", "Using placeholder ID")

    # =========================================================================
    # Test: Create Test Customer
    # =========================================================================

    def test_create_customer(self) -> Optional[str]:
        """Create a test customer for checkout testing."""
        print("\n[3/8] Creating Test Customer...")

        test_email = f"test-{int(time.time())}@debtstack-test.ai"

        try:
            customer = stripe.Customer.create(
                email=test_email,
                metadata={
                    "test": "true",
                    "created_by": "test_stripe_checkout_e2e.py",
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            self.log_result(
                "Create test customer",
                True,
                f"Created customer: {customer.id}",
                f"Email: {test_email}"
            )
            return customer.id
        except Exception as e:
            self.log_result("Create test customer", False, f"Error: {e}")
            return None

    # =========================================================================
    # Test: Pro Subscription Checkout Session
    # =========================================================================

    def test_pro_checkout_session(self, customer_id: str):
        """Test creating a Pro subscription checkout session."""
        print("\n[4/8] Testing Pro Subscription Checkout...")

        if not self.pro_price_id or self.pro_price_id.startswith("price_pro"):
            self.log_skip("Pro checkout session", "No real price ID configured")
            return None

        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=["card"],
                line_items=[{
                    "price": self.pro_price_id,
                    "quantity": 1,
                }],
                mode="subscription",
                success_url="https://debtstack.ai/dashboard?upgraded=pro",
                cancel_url="https://debtstack.ai/pricing",
                metadata={
                    "test": "true",
                    "tier": "pro",
                },
            )

            is_valid_url = session.url and session.url.startswith("https://checkout.stripe.com")

            self.log_result(
                "Pro checkout session",
                is_valid_url,
                f"Session created: {session.id}",
                f"URL: {session.url[:60]}..." if session.url else "No URL"
            )

            if self.verbose and session.url:
                print(f"\n  Pro Checkout URL (open in browser to test):")
                print(f"     {session.url}\n")

            return session

        except stripe.error.InvalidRequestError as e:
            self.log_result("Pro checkout session", False, f"Invalid request: {e}")
            return None
        except Exception as e:
            self.log_result("Pro checkout session", False, f"Error: {e}")
            return None

    # =========================================================================
    # Test: Business Subscription Checkout Session
    # =========================================================================

    def test_business_checkout_session(self, customer_id: str):
        """Test creating a Business subscription checkout session."""
        print("\n[5/8] Testing Business Subscription Checkout...")

        if not self.business_price_id or self.business_price_id.startswith("price_business"):
            self.log_skip("Business checkout session", "No real price ID configured")
            return None

        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=["card"],
                line_items=[{
                    "price": self.business_price_id,
                    "quantity": 1,
                }],
                mode="subscription",
                success_url="https://debtstack.ai/dashboard?upgraded=business",
                cancel_url="https://debtstack.ai/pricing",
                metadata={
                    "test": "true",
                    "tier": "business",
                },
            )

            is_valid_url = session.url and session.url.startswith("https://checkout.stripe.com")

            self.log_result(
                "Business checkout session",
                is_valid_url,
                f"Session created: {session.id}",
                f"URL: {session.url[:60]}..." if session.url else "No URL"
            )

            if self.verbose and session.url:
                print(f"\n  Business Checkout URL (open in browser to test):")
                print(f"     {session.url}\n")

            return session

        except stripe.error.InvalidRequestError as e:
            self.log_result("Business checkout session", False, f"Invalid request: {e}")
            return None
        except Exception as e:
            self.log_result("Business checkout session", False, f"Error: {e}")
            return None

    # =========================================================================
    # Test: Credit Package Checkout Session
    # =========================================================================

    def test_credit_checkout_session(self, customer_id: str, amount: int = 25):
        """Test creating a credit package checkout session."""
        print(f"\n[6/8] Testing ${amount} Credit Package Checkout...")

        price_id = self.credit_price_ids.get(amount)
        if not price_id or price_id.startswith("price_credits"):
            self.log_skip(f"${amount} credit checkout", "No real price ID configured")
            return None

        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=["card"],
                line_items=[{
                    "price": price_id,
                    "quantity": 1,
                }],
                mode="payment",  # One-time payment
                success_url="https://debtstack.ai/dashboard?credits=purchased",
                cancel_url="https://debtstack.ai/pricing",
                metadata={
                    "test": "true",
                    "type": "credit_purchase",
                    "credit_amount": str(amount),
                },
            )

            is_valid_url = session.url and session.url.startswith("https://checkout.stripe.com")

            self.log_result(
                f"${amount} credit checkout session",
                is_valid_url,
                f"Session created: {session.id}",
                f"URL: {session.url[:60]}..." if session.url else "No URL"
            )

            if self.verbose and session.url:
                print(f"\n  Credit Purchase URL (open in browser to test):")
                print(f"     {session.url}\n")

            return session

        except stripe.error.InvalidRequestError as e:
            self.log_result(f"${amount} credit checkout session", False, f"Invalid request: {e}")
            return None
        except Exception as e:
            self.log_result(f"${amount} credit checkout session", False, f"Error: {e}")
            return None

    # =========================================================================
    # Test: Webhook Signature Verification
    # =========================================================================

    def test_webhook_signature(self):
        """Test webhook signature verification."""
        print("\n[7/8] Testing Webhook Signature Verification...")

        if not self.webhook_secret:
            self.log_skip("Webhook signature", "STRIPE_WEBHOOK_SECRET not set")
            return

        # Create a test payload
        test_payload = json.dumps({
            "id": "evt_test_123",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "customer": "cus_test_123",
                }
            }
        })

        # Test with invalid signature
        try:
            stripe.Webhook.construct_event(
                test_payload,
                "invalid_signature",
                self.webhook_secret
            )
            self.log_result("Invalid signature rejected", False, "Should have raised error")
        except stripe.error.SignatureVerificationError:
            self.log_result("Invalid signature rejected", True, "Correctly rejected invalid signature")
        except Exception as e:
            self.log_result("Invalid signature rejected", False, f"Unexpected error: {e}")

        # Test signature generation (for reference)
        timestamp = int(time.time())
        signed_payload = f"{timestamp}.{test_payload}"

        import hmac
        import hashlib

        # Create valid signature
        signature = hmac.new(
            self.webhook_secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        sig_header = f"t={timestamp},v1={signature}"

        try:
            event = stripe.Webhook.construct_event(
                test_payload,
                sig_header,
                self.webhook_secret
            )
            self.log_result("Valid signature accepted", True, f"Event type: {event['type']}")
        except stripe.error.SignatureVerificationError as e:
            self.log_result("Valid signature accepted", False, f"Signature verification failed: {e}")
        except Exception as e:
            self.log_result("Valid signature accepted", False, f"Error: {e}")

    # =========================================================================
    # Test: Cleanup
    # =========================================================================

    def cleanup_test_customer(self, customer_id: str):
        """Delete test customer to clean up."""
        print("\n[8/8] Cleaning Up...")

        try:
            stripe.Customer.delete(customer_id)
            self.log_result("Delete test customer", True, f"Deleted: {customer_id}")
        except Exception as e:
            self.log_result("Delete test customer", False, f"Error: {e}")

    # =========================================================================
    # Run All Tests
    # =========================================================================

    def run_all_tests(self, cleanup: bool = True):
        """Run all end-to-end tests."""
        print("=" * 60)
        print("Stripe Checkout End-to-End Test")
        print("=" * 60)
        print(f"Mode: {'TEST' if self.is_test_mode else '** LIVE **'}")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Test 1: Connection
        if not self.test_stripe_connection():
            print("\n[ERROR] Cannot continue without Stripe connection")
            return False

        # Test 2: Price IDs
        self.test_price_ids_exist()

        # Test 3: Create customer
        customer_id = self.test_create_customer()
        if not customer_id:
            print("\n[ERROR] Cannot continue without test customer")
            return False

        try:
            # Test 4: Pro checkout
            self.test_pro_checkout_session(customer_id)

            # Test 5: Business checkout
            self.test_business_checkout_session(customer_id)

            # Test 6: Credit checkout
            self.test_credit_checkout_session(customer_id, 25)

            # Test 7: Webhook signature
            self.test_webhook_signature()

        finally:
            # Test 8: Cleanup
            if cleanup:
                self.cleanup_test_customer(customer_id)
            else:
                print(f"\n  [INFO] Test customer NOT deleted: {customer_id}")

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Passed:  {self.passed}")
        print(f"Failed:  {self.failed}")
        print(f"Skipped: {self.skipped}")
        print(f"Total:   {self.passed + self.failed + self.skipped}")

        if self.failed == 0:
            print("\n[PASS] ALL TESTS PASSED")
            return True
        else:
            print(f"\n[FAIL] {self.failed} TESTS FAILED")
            print("\nFailed tests:")
            for result in self.results:
                if result["passed"] is False:
                    print(f"  - {result['test']}: {result['message']}")
            return False


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end test for Stripe checkout flow"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output including checkout URLs"
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't delete test customer after tests"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow running with live Stripe key (dangerous!)"
    )
    args = parser.parse_args()

    # Safety check for live mode
    stripe_key = os.getenv("STRIPE_API_KEY", "")
    if not stripe_key.startswith("sk_test_") and not args.live:
        print("[ERROR] STRIPE_API_KEY is not a test key (sk_test_...)")
        print("   Use --live flag to run with live keys (DANGEROUS)")
        sys.exit(1)

    try:
        tester = StripeCheckoutTester(verbose=args.verbose)
        success = tester.run_all_tests(cleanup=not args.no_cleanup)
        sys.exit(0 if success else 1)
    except ValueError as e:
        print(f"[ERROR] Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
