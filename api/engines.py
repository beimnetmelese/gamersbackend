import random
from collections import Counter
from typing import Optional, Tuple
from .models import Game, GameParticipant, GameResult

CARD_RANKS = {
    'A': 14, 'K': 13, 'Q': 12, 'J': 11,
    '10': 10, '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3, '2': 2
}

class GameEngine:
    """
    Backend Game Resolution Engine enforcing rules for all game types with Earliest Bidder Tie-Breaker.
    """

    @staticmethod
    def resolve_game(game: Game) -> Tuple[Optional[GameParticipant], str]:
        # Always order participants by joined_at timestamp so earliest bidder breaks any tie!
        participants = list(game.participants.all().order_by('joined_at', 'id'))
        if not participants:
            return None, "No participants registered for this game."

        game_type = game.game_type

        if game_type == 'TREASURE_BOX':
            return GameEngine._resolve_treasure_box(game, participants)
        elif game_type == 'LOWEST_UNIQUE':
            return GameEngine._resolve_lowest_unique(participants)
        elif game_type == 'HIGHEST_CARD':
            return GameEngine._resolve_highest_card(participants)
        elif game_type == 'SECRET_NUMBER':
            return GameEngine._resolve_secret_number(game, participants)
        elif game_type == 'PRECISION_TIMER':
            return GameEngine._resolve_precision_timer(participants)
        elif game_type == 'HEAD_TO_HEAD':
            return GameEngine._resolve_head_to_head(participants)
        elif game_type == 'TOURNAMENT':
            return GameEngine._resolve_tournament(participants)
        else:
            return None, f"Unsupported game type: {game_type}"

    @staticmethod
    def _resolve_head_to_head(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not participants:
            return None, "No players in head-to-head match."
        # Earliest bidder wins tie
        winner = participants[0]
        return winner, f"Head-to-Head winner determined! @{winner.user.username} won the duel (Earliest Bidder)."

    @staticmethod
    def _resolve_tournament(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not participants:
            return None, "No participants in tournament bracket."
        winner = participants[0]
        return winner, f"Tournament completed! Grand Champion is @{winner.user.username} (Earliest Bidder)."

    @staticmethod
    def _resolve_treasure_box(game: Game, participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.selected_box is not None]
        if not valid_entries:
            return None, "No valid box selections submitted."
        
        # Determine winning treasure box independently from all boxes (including unselected boxes)
        total_boxes = max(game.total_boxes, game.max_participants, 50)
        if game.secret_target and str(game.secret_target).isdigit():
            target_box = int(game.secret_target)
        else:
            target_box = random.randint(1, total_boxes)
            game.secret_target = str(target_box)
            game.save()

        # Check for exact participant match
        exact_matches = [p for p in valid_entries if p.selected_box == target_box]
        if exact_matches:
            exact_matches.sort(key=lambda p: p.joined_at)
            winner = exact_matches[0]
            return winner, f"Winning Treasure Box was #{target_box}! Winner @{winner.user.username} picked the exact winning box!"
        
        # If no exact match, winner is participant closest to the winning box
        valid_entries.sort(key=lambda p: (abs(p.selected_box - target_box), p.joined_at))
        winner = valid_entries[0]
        return winner, f"Winning Treasure Box was #{target_box}! Winner @{winner.user.username} (Box #{winner.selected_box}) won as closest participant!"

    @staticmethod
    def _resolve_lowest_unique(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        numbers = [p.selected_number for p in participants if p.selected_number is not None]
        if not numbers:
            return None, "No valid number selections."

        counts = Counter(numbers)
        unique_numbers = [num for num, count in counts.items() if count == 1]

        if not unique_numbers:
            # Rule: If no unique number exists, refund participants
            return None, "NO_UNIQUE_NUMBER_FOUND"

        lowest_num = min(unique_numbers)
        # Find all participants who chose lowest unique number sorted by earliest timestamp
        matching = [p for p in participants if p.selected_number == lowest_num]
        matching.sort(key=lambda p: p.joined_at)
        winner = matching[0]
        return winner, f"Lowest unique number submitted was {lowest_num}. Winner: @{winner.user.username} (Earliest Bidder)."

    @staticmethod
    def _resolve_highest_card(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        cards = [p.selected_card for p in participants if p.selected_card]
        if not cards:
            return None, "No cards selected."

        counts = Counter(cards)
        unique_cards = [card for card, count in counts.items() if count == 1]

        if not unique_cards:
            return None, "NO_UNIQUE_CARD_FOUND"

        def card_score(c: str) -> int:
            val = c[:-1] if len(c) > 1 else c
            return CARD_RANKS.get(val.upper(), 0)

        highest_card = max(unique_cards, key=card_score)
        matching = [p for p in participants if p.selected_card == highest_card]
        matching.sort(key=lambda p: p.joined_at)
        winner = matching[0]
        return winner, f"Highest unique card was {highest_card}. Winner: @{winner.user.username} (Earliest Bidder)."

    @staticmethod
    def _resolve_secret_number(game: Game, participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not game.secret_target:
            target = random.randint(1, 100)
            game.secret_target = str(target)
            game.save()
        else:
            target = int(game.secret_target)

        valid_entries = [p for p in participants if p.selected_number is not None]
        if not valid_entries:
            return None, "No valid guesses."

        # Sort by closest guess to target first, then earliest joined_at timestamp
        valid_entries.sort(key=lambda p: (abs(p.selected_number - target), p.joined_at))
        winner = valid_entries[0]
        diff = abs(winner.selected_number - target)
        return winner, f"Target was {target}. Winner @{winner.user.username} guessed {winner.selected_number} (off by {diff}, Earliest Bidder Tie-Breaker)."

    @staticmethod
    def _resolve_precision_timer(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.timer_delta_ms is not None]
        if not valid_entries:
            return None, "No timer attempts recorded."

        # Sort by smallest millisecond delta first, then earliest joined_at timestamp
        valid_entries.sort(key=lambda p: (abs(p.timer_delta_ms), p.joined_at))
        winner = valid_entries[0]
        return winner, f"Winner @{winner.user.username} achieved closest time with delta of {winner.timer_delta_ms} ms (Earliest Bidder Tie-Breaker)."
