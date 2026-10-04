import time
import requests
import json
from decimal import Decimal
from typing import Optional, Dict, Any, List, Tuple
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from api.models import (
    UserProfile, Wallet, PaymentSubmission, WithdrawalRequest, Game, GameParticipant, GameResult
)
from api.services import WalletService, PaymentService, WithdrawalService, NotificationService, AuthService
from api.telegram_auth import get_or_create_telegram_user


def sanitize_web_app_url(url: str) -> str:
    """
    Telegram Bot API strictly mandates valid public HTTPS URLs for WebApp buttons.
    Converts http:// to https:// and defaults localhost to a valid Vercel domain.
    """
    clean_url = (url or "").strip()
    if not clean_url or "localhost" in clean_url or "127.0.0.1" in clean_url:
        return "https://addisgigs-game.vercel.app"
    if clean_url.startswith("http://"):
        clean_url = "https://" + clean_url[7:]
    return clean_url


class Command(BaseCommand):
    help = 'Runs the official AddisGigs Telegram Bot daemon with instant auto-registration, HTTPS WebApp launcher, and clean single-message responses.'

    def handle(self, *args, **options):
        bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '').strip()
        if not bot_token:
            self.stdout.write(self.style.ERROR("❌ TELEGRAM_BOT_TOKEN is missing in environment or settings.py"))
            return

        raw_app_url = getattr(settings, 'TELEGRAM_WEB_APP_URL', 'http://localhost:5173')
        web_app_url = sanitize_web_app_url(raw_app_url)
        api_url = f"https://api.telegram.org/bot{bot_token}"
        admin_ids = [str(aid).strip() for aid in getattr(settings, 'TELEGRAM_ADMIN_IDS', [])]

        self.stdout.write(self.style.SUCCESS("=================================================="))
        self.stdout.write(self.style.SUCCESS("🤖 ADDISGIGS TELEGRAM BOT DAEMON ACTIVE"))
        self.stdout.write(self.style.SUCCESS(f"🔗 Configured WebApp URL: {web_app_url}"))
        self.stdout.write(self.style.SUCCESS(f"👑 Whitelisted Admin Telegram IDs: {admin_ids}"))
        self.stdout.write(self.style.SUCCESS("=================================================="))

        offset = 0

        while True:
            try:
                response = requests.get(
                    f"{api_url}/getUpdates",
                    params={"offset": offset, "timeout": 20},
                    timeout=25
                )

                if response.status_code != 200:
                    self.stdout.write(self.style.WARNING(f"⚠️ getUpdates returned HTTP {response.status_code}. Retrying in 3s..."))
                    time.sleep(3)
                    continue

                data = response.json()
                if not data.get("ok"):
                    time.sleep(3)
                    continue

                for update in data.get("result", []):
                    offset = max(offset, update["update_id"] + 1)
                    self.route_update(api_url, web_app_url, admin_ids, update)

            except requests.RequestException as req_err:
                self.stdout.write(self.style.WARNING(f"🌐 Reconnecting bot loop: {req_err}..."))
                time.sleep(4)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"❌ Error in processing loop: {e}"))
                time.sleep(2)

    # =========================================================================
    # UPDATE ROUTER & DISPATCHER
    # =========================================================================
    def route_update(self, api_url: str, web_app_url: str, admin_ids: list, update: dict):
        if "message" in update:
            self.handle_message(api_url, web_app_url, admin_ids, update["message"])
        elif "callback_query" in update:
            self.handle_callback_query(api_url, web_app_url, admin_ids, update["callback_query"])

    def handle_message(self, api_url: str, web_app_url: str, admin_ids: list, message: dict):
        chat_id = message.get("chat", {}).get("id")
        from_user = message.get("from", {})
        raw_text = (message.get("text") or "").strip()

        if not chat_id or not from_user or not raw_text:
            return

        tg_id = str(from_user.get("id"))
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")

        # Auto-register user on-the-fly on ANY interaction so commands never fail
        user, profile, _ = get_or_create_telegram_user(
            telegram_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name
        )

        is_admin = tg_id in admin_ids or profile.role == 'ADMIN' or user.is_staff
        lower_text = raw_text.lower()

        self.stdout.write(self.style.SUCCESS(f"📩 [{tg_id}] {first_name}: {raw_text}"))

        # 1. LAUNCH MINI APP / START COMMAND
        if lower_text.startswith("/start") or "launch" in lower_text or "mini app" in lower_text or "open app" in lower_text or "play" in lower_text:
            self.cmd_start(api_url, web_app_url, chat_id, tg_id, username, first_name, user, profile, is_admin)

        # 2. USER PROFILE & STATS
        elif lower_text.startswith("/profile") or "profile" in lower_text or "account" in lower_text:
            self.cmd_profile(api_url, web_app_url, chat_id, user, profile, is_admin)

        # 3. WALLET & BALANCE DETAILS
        elif lower_text.startswith("/wallet") or lower_text.startswith("/balance") or "wallet" in lower_text or "balance" in lower_text or "funds" in lower_text:
            self.cmd_wallet(api_url, web_app_url, chat_id, user)

        # 4. HELP & SUPPORT
        elif lower_text.startswith("/help") or lower_text.startswith("/support") or "help" in lower_text or "support" in lower_text or "faq" in lower_text:
            self.cmd_help(api_url, web_app_url, chat_id, is_admin)

        # 5. USER REGISTRATION
        elif lower_text.startswith("/register") or "register" in lower_text:
            self.do_register(api_url, web_app_url, chat_id, tg_id, username, first_name, last_name, admin_ids)

        # 6. RESTRICTED ADMIN COMMANDS
        elif lower_text.startswith("/admin") or "admin" in lower_text:
            if is_admin:
                self.cmd_admin_panel(api_url, chat_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: This command is reserved for platform administrators.")

        elif lower_text.startswith("/pending_deposits") or lower_text.startswith("/deposits") or "pending deposits" in lower_text:
            if is_admin:
                self.cmd_list_deposits(api_url, chat_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: This command is reserved for platform administrators.")

        elif lower_text.startswith("/pending_withdrawals") or lower_text.startswith("/withdrawals") or "pending withdrawals" in lower_text:
            if is_admin:
                self.cmd_list_withdrawals(api_url, chat_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: This command is reserved for platform administrators.")

        elif lower_text.startswith("/approve_deposit"):
            if is_admin:
                parts = raw_text.split()
                dep_id = parts[1] if len(parts) > 1 else None
                self.cmd_approve_deposit(api_url, chat_id, user, dep_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")

        elif lower_text.startswith("/reject_deposit"):
            if is_admin:
                parts = raw_text.split()
                dep_id = parts[1] if len(parts) > 1 else None
                self.cmd_reject_deposit(api_url, chat_id, user, dep_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")

        elif lower_text.startswith("/approve_withdrawal"):
            if is_admin:
                parts = raw_text.split()
                wd_id = parts[1] if len(parts) > 1 else None
                self.cmd_approve_withdrawal(api_url, chat_id, user, wd_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")

        elif lower_text.startswith("/reject_withdrawal"):
            if is_admin:
                parts = raw_text.split()
                wd_id = parts[1] if len(parts) > 1 else None
                self.cmd_reject_withdrawal(api_url, chat_id, user, wd_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")

        else:
            # Fallback for unrecognized messages
            self.cmd_start(api_url, web_app_url, chat_id, tg_id, username, first_name, user, profile, is_admin)

    def handle_callback_query(self, api_url: str, web_app_url: str, admin_ids: list, cb: dict):
        cb_id = cb.get("id")
        cb_data = cb.get("data", "")
        from_user = cb.get("from", {})
        tg_id = str(from_user.get("id"))
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id")

        # Answer callback query immediately to stop Telegram loading spinner
        try:
            requests.post(f"{api_url}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=4)
        except Exception:
            pass

        user, profile, _ = get_or_create_telegram_user(
            telegram_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name
        )
        is_admin = tg_id in admin_ids or profile.role == 'ADMIN' or user.is_staff

        if cb_data == "register_account":
            self.do_register(api_url, web_app_url, chat_id, tg_id, username, first_name, last_name, admin_ids)
        elif cb_data == "show_profile":
            self.cmd_profile(api_url, web_app_url, chat_id, user, profile, is_admin)
        elif cb_data == "show_wallet":
            self.cmd_wallet(api_url, web_app_url, chat_id, user)
        elif cb_data == "show_help":
            self.cmd_help(api_url, web_app_url, chat_id, is_admin)
        elif cb_data.startswith("app_dep_"):
            dep_id = cb_data.replace("app_dep_", "")
            if is_admin:
                self.cmd_approve_deposit(api_url, chat_id, user, dep_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")
        elif cb_data.startswith("rej_dep_"):
            dep_id = cb_data.replace("rej_dep_", "")
            if is_admin:
                self.cmd_reject_deposit(api_url, chat_id, user, dep_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")
        elif cb_data.startswith("app_wd_"):
            wd_id = cb_data.replace("app_wd_", "")
            if is_admin:
                self.cmd_approve_withdrawal(api_url, chat_id, user, wd_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")
        elif cb_data.startswith("rej_wd_"):
            wd_id = cb_data.replace("rej_wd_", "")
            if is_admin:
                self.cmd_reject_withdrawal(api_url, chat_id, user, wd_id)
            else:
                self.send_message(api_url, chat_id, "⛔ Restricted Access: Admin authorization required.")

    # =========================================================================
    # SMART COMMAND HANDLERS
    # =========================================================================
    def cmd_start(self, api_url: str, web_app_url: str, chat_id: int, tg_id: str, username: str, first_name: str, user, profile: UserProfile, is_admin: bool):
        wallet = WalletService.get_or_create_wallet(user)
        stats = AuthService.get_user_stats(user)

        display_name = first_name or profile.telegram_first_name or username or user.username
        username_display = f"@{profile.telegram_username}" if profile.telegram_username else user.username

        msg = f"👋 <b>Welcome to AddisGigs Games, {display_name}!</b> 🎮\n\n"
        msg += f"👤 <b>Account:</b> {username_display} (ID: <code>{tg_id}</code>)\n"
        msg += f"👑 <b>Role:</b> {profile.role}\n"
        msg += f"💰 <b>Available Balance:</b> {wallet.available_balance} ETB (Total: {wallet.balance} ETB)\n"
        msg += f"🎮 <b>Games Played:</b> {stats.get('games_played', 0)} | 🏆 <b>Wins:</b> {stats.get('games_won', 0)}\n\n"
        msg += "Tap <b>🎮 Launch AddisGigs Mini App</b> below to play live challenges!"

        inline_keyboard = [
            [
                {
                    "text": "🎮 Launch AddisGigs Mini App",
                    "web_app": {"url": web_app_url}
                }
            ],
            [
                {"text": "💰 Wallet & Funds", "callback_data": "show_wallet"},
                {"text": "👤 My Profile", "callback_data": "show_profile"}
            ],
            [
                {"text": "ℹ️ Help & Support", "callback_data": "show_help"}
            ]
        ]

        persistent_keyboard = [
            [{"text": "🎮 Launch Mini App", "web_app": {"url": web_app_url}}, "💰 Wallet"],
            ["👤 Profile", "ℹ️ Help"]
        ]
        if is_admin:
            persistent_keyboard.append(["⚡ Admin Panel", "📥 Pending Deposits"])

        self.send_clean_message(api_url, chat_id, msg, inline_keyboard, persistent_keyboard)

    def do_register(self, api_url: str, web_app_url: str, chat_id: int, tg_id: str, username: str, first_name: str, last_name: str, admin_ids: list):
        try:
            user, profile, _ = get_or_create_telegram_user(
                telegram_id=tg_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            wallet = WalletService.get_or_create_wallet(user)
            is_admin = tg_id in admin_ids or profile.role == 'ADMIN'
            display_name = first_name or profile.telegram_first_name or username or user.username

            msg = f"🎉 <b>ACCOUNT REGISTERED SUCCESSFULLY!</b>\n\n"
            msg += f"Welcome to AddisGigs Games, <b>{display_name}</b>!\n"
            msg += f"Your ETB wallet and account have been activated.\n\n"
            msg += f"👤 <b>Telegram ID:</b> <code>{tg_id}</code>\n"
            msg += f"💰 <b>Initial Balance:</b> {wallet.balance} ETB\n"
            msg += f"👑 <b>Role:</b> {profile.role}\n\n"
            msg += "Tap below to launch the Mini App and join live competitions:"

            inline_keyboard = [
                [
                    {
                        "text": "🎮 Launch AddisGigs Mini App",
                        "web_app": {"url": web_app_url}
                    }
                ],
                [
                    {"text": "💰 Wallet & Deposit", "callback_data": "show_wallet"},
                    {"text": "👤 View Profile", "callback_data": "show_profile"}
                ]
            ]

            persistent_keyboard = [
                [{"text": "🎮 Launch Mini App", "web_app": {"url": web_app_url}}, "💰 Wallet"],
                ["👤 Profile", "ℹ️ Help"]
            ]
            if is_admin:
                persistent_keyboard.append(["⚡ Admin Panel", "📥 Pending Deposits"])

            self.send_clean_message(api_url, chat_id, msg, inline_keyboard, persistent_keyboard)

        except Exception as e:
            self.send_message(api_url, chat_id, f"❌ Registration error: {str(e)}")

    def cmd_profile(self, api_url: str, web_app_url: str, chat_id: int, user, profile: UserProfile, is_admin: bool):
        stats = AuthService.get_user_stats(user)
        wallet = WalletService.get_or_create_wallet(user)

        msg = f"👤 <b>ADDISGIGS PLAYER PROFILE</b>\n\n"
        msg += f"• <b>Name:</b> {user.first_name or profile.telegram_first_name or 'Player'}\n"
        msg += f"• <b>Username:</b> @{profile.telegram_username or user.username}\n"
        msg += f"• <b>Telegram ID:</b> <code>{profile.telegram_id or chat_id}</code>\n"
        msg += f"• <b>Account Status:</b> {profile.account_status} ✅\n"
        msg += f"• <b>Role:</b> {profile.role}\n"
        msg += f"• <b>Available Balance:</b> {wallet.available_balance} ETB\n"
        msg += f"• <b>Reserved Balance:</b> {wallet.reserved_balance} ETB\n"
        msg += f"• <b>Games Joined:</b> {stats.get('games_played', 0)}\n"
        msg += f"• <b>Total Wins:</b> {stats.get('games_won', 0)}\n"
        msg += f"• <b>Win Rate:</b> {stats.get('win_rate', 0)}%\n"

        inline_keyboard = [
            [
                {
                    "text": "🎮 Launch AddisGigs Mini App",
                    "web_app": {"url": web_app_url}
                }
            ],
            [
                {"text": "💰 Wallet & Balance", "callback_data": "show_wallet"}
            ]
        ]
        self.send_clean_message(api_url, chat_id, msg, inline_keyboard)

    def cmd_wallet(self, api_url: str, web_app_url: str, chat_id: int, user):
        wallet = WalletService.get_or_create_wallet(user)
        telebirr_acc = getattr(settings, 'PAYMENT_TELEBIRR_ACCOUNT', '0911223344')
        telebirr_name = getattr(settings, 'PAYMENT_TELEBIRR_ACCOUNT_NAME', getattr(settings, 'PAYMENT_ACCOUNT_NAME', 'Beiment Melese'))
        cbe_acc = getattr(settings, 'PAYMENT_CBE_ACCOUNT', '1000123456789')
        cbe_name = getattr(settings, 'PAYMENT_CBE_ACCOUNT_NAME', getattr(settings, 'PAYMENT_ACCOUNT_NAME', 'Beiment Melese'))

        msg = f"💰 <b>ADDISGIGS WALLET & FUNDS</b>\n\n"
        msg += f"• <b>Available Balance:</b> {wallet.available_balance} ETB\n"
        msg += f"• <b>Reserved Balance:</b> {wallet.reserved_balance} ETB\n"
        msg += f"• <b>Total Balance:</b> {wallet.balance} ETB\n\n"
        msg += f"💳 <b>Deposit Payment Accounts:</b>\n"
        msg += f"• <b>Telebirr:</b> <code>{telebirr_acc}</code> ({telebirr_name})\n"
        msg += f"• <b>CBE Bank:</b> <code>{cbe_acc}</code> ({cbe_name})\n\n"
        msg += "Open the Mini App to submit instant deposit receipts or request withdrawals."

        inline_keyboard = [
            [
                {
                    "text": "🎮 Open Wallet in Mini App",
                    "web_app": {"url": web_app_url}
                }
            ]
        ]
        self.send_clean_message(api_url, chat_id, msg, inline_keyboard)

    def cmd_help(self, api_url: str, web_app_url: str, chat_id: int, is_admin: bool):
        msg = "ℹ️ <b>ADDISGIGS GAMES HELP & SUPPORT</b>\n\n"
        msg += "<b>Standard Commands:</b>\n"
        msg += "• /start - Welcome & Mini App Launcher\n"
        msg += "• /profile - Profile details & Win/Loss statistics\n"
        msg += "• /wallet - Check available ETB balance & payment accounts\n"
        msg += "• /register - Register Telegram ID account\n"
        msg += "• /help - Support and command list\n\n"

        if is_admin:
            msg += "<b>👑 Admin Commands (Restricted):</b>\n"
            msg += "• /admin - View Admin Control Dashboard\n"
            msg += "• /pending_deposits - List pending deposit requests\n"
            msg += "• /pending_withdrawals - List pending withdrawal requests\n"
            msg += "• /approve_deposit &lt;id&gt; - Approve deposit\n"
            msg += "• /reject_deposit &lt;id&gt; - Reject deposit\n"
            msg += "• /approve_withdrawal &lt;id&gt; - Approve withdrawal\n"
            msg += "• /reject_withdrawal &lt;id&gt; - Reject withdrawal\n"

        inline_keyboard = [
            [
                {
                    "text": "🎮 Launch AddisGigs Mini App",
                    "web_app": {"url": web_app_url}
                }
            ]
        ]
        self.send_clean_message(api_url, chat_id, msg, inline_keyboard)

    def cmd_admin_panel(self, api_url: str, chat_id: int):
        pending_deps = PaymentSubmission.objects.filter(status='PENDING').count()
        pending_wds = WithdrawalRequest.objects.filter(status='PENDING').count()
        total_games = Game.objects.count()

        msg = "⚡ <b>AddisGigs Admin Control Dashboard</b> 👑\n\n"
        msg += f"• <b>Pending Deposit Receipts:</b> {pending_deps}\n"
        msg += f"• <b>Pending Withdrawals:</b> {pending_wds}\n"
        msg += f"• <b>Total Competitions:</b> {total_games}\n\n"
        msg += "Use /pending_deposits or /pending_withdrawals to review requests."

        self.send_message(api_url, chat_id, msg)

    def cmd_list_deposits(self, api_url: str, chat_id: int):
        pending = PaymentSubmission.objects.filter(status='PENDING').order_by('-submitted_at')[:10]
        if not pending.exists():
            self.send_message(api_url, chat_id, "✅ No pending deposits at this time.")
            return

        for dep in pending:
            msg = f"📥 <b>Deposit Request #{dep.id}</b>\n"
            msg += f"• User: {dep.user.username}\n"
            msg += f"• Amount: {dep.amount} ETB\n"
            msg += f"• Method: {dep.payment_method} ({dep.bank.upper()})\n"
            msg += f"• Tx ID: <code>{dep.transaction_id}</code>\n\n"
            msg += f"Actions: /approve_deposit {dep.id} | /reject_deposit {dep.id}"

            inline_kb = [
                [
                    {"text": "✅ Approve", "callback_data": f"app_dep_{dep.id}"},
                    {"text": "❌ Reject", "callback_data": f"rej_dep_{dep.id}"}
                ]
            ]
            payload = {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": inline_kb}
            }
            self.send_payload(api_url, payload)

    def cmd_list_withdrawals(self, api_url: str, chat_id: int):
        pending = WithdrawalRequest.objects.filter(status='PENDING').order_by('-submitted_at')[:10]
        if not pending.exists():
            self.send_message(api_url, chat_id, "✅ No pending withdrawals at this time.")
            return

        for wd in pending:
            msg = f"💸 <b>Withdrawal Request #{wd.id}</b>\n"
            msg += f"• User: {wd.user.username}\n"
            msg += f"• Amount: {wd.amount} ETB\n"
            msg += f"• Method: {wd.withdrawal_method}\n"
            msg += f"• Account: {wd.account_number} ({wd.account_name})\n\n"
            msg += f"Actions: /approve_withdrawal {wd.id} | /reject_withdrawal {wd.id}"

            inline_kb = [
                [
                    {"text": "✅ Approve", "callback_data": f"app_wd_{wd.id}"},
                    {"text": "❌ Reject", "callback_data": f"rej_wd_{wd.id}"}
                ]
            ]
            payload = {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": inline_kb}
            }
            self.send_payload(api_url, payload)

    def cmd_approve_deposit(self, api_url: str, chat_id: int, admin_user, dep_id: Optional[str]):
        if not dep_id or not dep_id.isdigit():
            self.send_message(api_url, chat_id, "⚠️ Usage: /approve_deposit <id>")
            return
        ok, msg, _ = PaymentService.approve_deposit(int(dep_id), admin_user, "Approved via Telegram Bot")
        prefix = "✅" if ok else "❌"
        self.send_message(api_url, chat_id, f"{prefix} {msg}")

    def cmd_reject_deposit(self, api_url: str, chat_id: int, admin_user, dep_id: Optional[str]):
        if not dep_id or not dep_id.isdigit():
            self.send_message(api_url, chat_id, "⚠️ Usage: /reject_deposit <id>")
            return
        ok, msg, _ = PaymentService.reject_deposit(int(dep_id), admin_user, "Rejected via Telegram Bot")
        prefix = "✅" if ok else "❌"
        self.send_message(api_url, chat_id, f"{prefix} {msg}")

    def cmd_approve_withdrawal(self, api_url: str, chat_id: int, admin_user, wd_id: Optional[str]):
        if not wd_id or not wd_id.isdigit():
            self.send_message(api_url, chat_id, "⚠️ Usage: /approve_withdrawal <id>")
            return
        ok, msg, _ = WithdrawalService.approve_withdrawal(int(wd_id), admin_user, "Approved via Telegram Bot")
        prefix = "✅" if ok else "❌"
        self.send_message(api_url, chat_id, f"{prefix} {msg}")

    def cmd_reject_withdrawal(self, api_url: str, chat_id: int, admin_user, wd_id: Optional[str]):
        if not wd_id or not wd_id.isdigit():
            self.send_message(api_url, chat_id, "⚠️ Usage: /reject_withdrawal <id>")
            return
        ok, msg, _ = WithdrawalService.reject_withdrawal(int(wd_id), admin_user, "Rejected via Telegram Bot")
        prefix = "✅" if ok else "❌"
        self.send_message(api_url, chat_id, f"{prefix} {msg}")

    # =========================================================================
    # TELEGRAM BOT MESSAGING UTILITIES
    # =========================================================================
    def send_message(self, api_url: str, chat_id: int, text: str):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        self.send_payload(api_url, payload)

    def send_clean_message(self, api_url: str, chat_id: int, text: str, inline_keyboard: Optional[list] = None, persistent_keyboard: Optional[list] = None):
        """
        Sends a response message containing inline keyboard buttons (e.g. WebApp launch)
        and updates persistent reply keyboard if provided without violating Telegram API markup constraints.
        """
        if inline_keyboard:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": inline_keyboard
                }
            }
            self.send_payload(api_url, payload)

            if persistent_keyboard:
                formatted_keyboard = []
                for row in persistent_keyboard:
                    formatted_row = []
                    for item in row:
                        if isinstance(item, dict):
                            formatted_row.append(item)
                        else:
                            formatted_row.append({"text": str(item)})
                    formatted_keyboard.append(formatted_row)

                menu_payload = {
                    "chat_id": chat_id,
                    "text": "📱 <i>Quick menu updated below:</i>",
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "keyboard": formatted_keyboard,
                        "resize_keyboard": True
                    }
                }
                self.send_payload(api_url, menu_payload)

        elif persistent_keyboard:
            formatted_keyboard = []
            for row in persistent_keyboard:
                formatted_row = []
                for item in row:
                    if isinstance(item, dict):
                        formatted_row.append(item)
                    else:
                        formatted_row.append({"text": str(item)})
                formatted_keyboard.append(formatted_row)

            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "keyboard": formatted_keyboard,
                    "resize_keyboard": True
                }
            }
            self.send_payload(api_url, payload)

        else:
            self.send_message(api_url, chat_id, text)

    def send_payload(self, api_url: str, payload: dict):
        try:
            res = requests.post(f"{api_url}/sendMessage", json=payload, timeout=8)
            if res.status_code != 200:
                self.stdout.write(self.style.ERROR(f"❌ Telegram API Error ({res.status_code}): {res.text}"))
                
                # Auto-fallback if web_app URL was rejected by Telegram API (e.g. invalid localhost domain)
                if res.status_code == 400 and ("BUTTON_URL_INVALID" in res.text or "WEB_APP" in res.text or "url" in res.text.lower()):
                    self.stdout.write(self.style.WARNING("🔄 Retrying payload with fallback URL button..."))
                    fallback_payload = json.loads(json.dumps(payload))
                    if "reply_markup" in fallback_payload and "inline_keyboard" in fallback_payload["reply_markup"]:
                        for row in fallback_payload["reply_markup"]["inline_keyboard"]:
                            for btn in row:
                                if "web_app" in btn:
                                    wa_url = btn["web_app"].get("url", "https://addisgigs-game.vercel.app")
                                    del btn["web_app"]
                                    btn["url"] = wa_url
                    res2 = requests.post(f"{api_url}/sendMessage", json=fallback_payload, timeout=8)
                    if res2.status_code == 200:
                        self.stdout.write(self.style.SUCCESS("✅ Payload sent successfully with fallback URL button."))
        except Exception as err:
            self.stdout.write(self.style.ERROR(f"❌ Failed to send Telegram payload: {err}"))
