import hmac
import hashlib
import json
import urllib.parse
from typing import Tuple, Dict, Any, Optional
from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework.authtoken.models import Token
from .models import UserProfile
from .services import WalletService


def verify_telegram_init_data(init_data_raw: str, bot_token: Optional[str] = None) -> Tuple[bool, Dict[str, Any], str]:
    """
    Verifies Telegram WebApp initData query string using HMAC-SHA256.
    Returns (is_valid, parsed_data_dict, message).
    If TELEGRAM_VERIFY_REQUESTS is False in settings, bypasses verification for local dev testing.
    """
    app_mode = getattr(settings, 'APP_MODE', 'test')
    verify_requests = getattr(settings, 'TELEGRAM_VERIFY_REQUESTS', True)

    if app_mode == 'test' or not verify_requests:
        # Test mode: Verification bypassed for local browser testing
        parsed = dict(urllib.parse.parse_qsl(init_data_raw or '', keep_blank_values=True))
        user_data = {}
        if 'user' in parsed:
            try:
                user_data = json.loads(parsed['user'])
            except Exception:
                pass
        parsed['user_obj'] = user_data
        return True, parsed, f"Test mode active (APP_MODE={app_mode}): Verification bypassed"

    if not init_data_raw:
        return False, {}, "Missing Telegram initData"

    token = bot_token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return False, {}, "Telegram bot token is not configured on server"

    try:
        parsed_qsl = urllib.parse.parse_qsl(init_data_raw, keep_blank_values=True)
        data_dict = dict(parsed_qsl)
        
        received_hash = data_dict.pop('hash', None)
        if not received_hash:
            return False, {}, "Missing hash parameter in initData"

        # Build data check string: key=value sorted alphabetically separated by newline
        data_check_list = [f"{k}={v}" for k, v in sorted(data_dict.items())]
        data_check_string = "\n".join(data_check_list)

        # secret_key = HMAC_SHA256("WebAppData", bot_token)
        secret_key = hmac.new(b"WebAppData", token.encode('utf-8'), hashlib.sha256).digest()
        
        # calculated_hash = HMAC_SHA256(secret_key, data_check_string).hex()
        calculated_hash = hmac.new(secret_key, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return False, {}, "Invalid initData HMAC signature"

        user_data = {}
        if 'user' in data_dict:
            try:
                user_data = json.loads(data_dict['user'])
            except Exception:
                pass
        data_dict['user_obj'] = user_data

        return True, data_dict, "Successfully verified Telegram initData"
    except Exception as e:
        return False, {}, f"Verification exception: {str(e)}"


def get_or_create_telegram_user(
    telegram_id: str,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    photo_url: Optional[str] = None,
    role: str = 'USER'
) -> Tuple[User, UserProfile, bool]:
    """
    Looks up or creates a user primarily by unique Telegram User ID (stored as string).
    Handles missing optional Telegram fields (username, first_name, last_name, photo_url) gracefully.
    Checks multi-admin whitelist settings TELEGRAM_ADMIN_IDS and elevates user to ADMIN if matched.
    """
    telegram_id_str = str(telegram_id).strip()
    if not telegram_id_str:
        raise ValueError("Telegram User ID cannot be empty.")

    # Check multi-admin configuration whitelist
    admin_ids = [str(aid).strip() for aid in getattr(settings, 'TELEGRAM_ADMIN_IDS', [])]
    is_admin = telegram_id_str in admin_ids

    # Gracefully sanitize optional Telegram fields without failing if null or missing
    safe_username = str(username).strip() if username else ''
    safe_first_name = str(first_name).strip() if first_name else ''
    safe_last_name = str(last_name).strip() if last_name else ''
    safe_photo_url = str(photo_url).strip() if photo_url else ''

    # Search existing UserProfile primarily by telegram_id
    profile = UserProfile.objects.filter(telegram_id=telegram_id_str).select_related('user').first()

    created = False
    if profile:
        user = profile.user
        updated = False
        if safe_username and profile.telegram_username != safe_username:
            profile.telegram_username = safe_username
            updated = True
        if safe_first_name and profile.telegram_first_name != safe_first_name:
            profile.telegram_first_name = safe_first_name
            updated = True
        if safe_photo_url and profile.avatar_url != safe_photo_url:
            profile.avatar_url = safe_photo_url
            updated = True
        
        # If user is in TELEGRAM_ADMIN_IDS, ensure they have ADMIN role
        if is_admin and (profile.role != 'ADMIN' or not user.is_staff):
            profile.role = 'ADMIN'
            user.is_staff = True
            user.save()
            updated = True

        if updated:
            profile.save()
    else:
        # Build Django User username
        base_username = safe_username if safe_username else f"tg_{telegram_id_str}"
        unique_username = base_username
        counter = 1
        while User.objects.filter(username__iexact=unique_username).exists():
            unique_username = f"{base_username}_{counter}"
            counter += 1

        with transaction.atomic():
            user = User.objects.create_user(
                username=unique_username,
                first_name=safe_first_name[:150] if safe_first_name else base_username[:150],
                last_name=safe_last_name[:150],
                is_staff=is_admin
            )
            profile = UserProfile.objects.create(
                user=user,
                telegram_id=telegram_id_str,
                telegram_username=safe_username,
                telegram_first_name=safe_first_name,
                telegram_chat_id=telegram_id_str,
                avatar_url=safe_photo_url,
                role='ADMIN' if is_admin else role
            )
            WalletService.get_or_create_wallet(user)
            created = True

    return user, profile, created
