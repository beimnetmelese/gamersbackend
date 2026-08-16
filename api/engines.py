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
    Backend Game Resolution Engine enforcing rules for all 6 game types.
    """

    @staticmethod
    def resolve_game(game: Game) -> Tuple[Optional[GameParticipant], str]:
        participants = list(game.participants.all())
        if not participants:
            return None, "No participants registered for this game."

        game_type = game.game_type

        if game_type == 'TREASURE_BOX':
            return GameEngine._resolve_treasure_box(participants)
        elif game_type == 'LOWEST_UNIQUE':
            return GameEngine._resolve_lowest_unique(participants)
        elif game_type == 'HIGHEST_CARD':
            return GameEngine._resolve_highest_card(participants)
        elif game_type == 'SECRET_NUMBER':
            return GameEngine._resolve_secret_number(game, participants)
        elif game_type == 'PREDICTION':
            return GameEngine._resolve_prediction(game, participants)
        elif game_type == 'PRECISION_TIMER':
            return GameEngine._resolve_precision_timer(participants)
        else:
            return None, f"Unsupported game type: {game_type}"

    @staticmethod
    def _resolve_treasure_box(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        # Filter participants with valid box selection
        valid_entries = [p for p in participants if p.selected_box is not None]
        if not valid_entries:
            return None, "No valid box selections submitted."
        
        # Backend randomly selects ONLY from boxes actually chosen by participants
        winner = random.choice(valid_entries)
        return winner, f"Box #{winner.selected_box} selected as winning treasure box!"

    @staticmethod
    def _resolve_lowest_unique(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        numbers = [p.selected_number for p in participants if p.selected_number is not None]
        if not numbers:
            return None, "No valid number selections."

        counts = Counter(numbers)
        unique_numbers = [num for num, count in counts.items() if count == 1]

        if not unique_numbers:
            # Rule: If no unique number exists, refund participants or extend
            return None, "NO_UNIQUE_NUMBER_FOUND"

        lowest_num = min(unique_numbers)
        winner = next(p for p in participants if p.selected_number == lowest_num)
        return winner, f"Lowest unique number submitted was {lowest_num}."

    @staticmethod
    def _resolve_highest_card(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        cards = [p.selected_card for p in participants if p.selected_card]
        if not cards:
            return None, "No cards selected."

        counts = Counter(cards)
        unique_cards = [card for card, count in counts.items() if count == 1]

        if not unique_cards:
            return None, "NO_UNIQUE_CARD_FOUND"

        # Determine card rank score
        def card_score(c: str) -> int:
            val = c[:-1] if len(c) > 1 else c # handle suit or single rank
            return CARD_RANKS.get(val.upper(), 0)

        highest_card = max(unique_cards, key=card_score)
        winner = next(p for p in participants if p.selected_card == highest_card)
        return winner, f"Highest unique card was {highest_card}."

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

        winner = min(valid_entries, key=lambda p: abs(p.selected_number - target))
        diff = abs(winner.selected_number - target)
        return winner, f"Target was {target}. Winner guessed {winner.selected_number} (off by {diff})."

    @staticmethod
    def _resolve_prediction(game: Game, participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not game.secret_target:
            return None, "Actual prediction result has not been recorded by admin."
        
        target = float(game.secret_target)
        valid_entries = [p for p in participants if p.prediction_answer is not None]
        if not valid_entries:
            return None, "No predictions submitted."

        winner = min(valid_entries, key=lambda p: abs(p.prediction_answer - target))
        diff = abs(winner.prediction_answer - target)
        return winner, f"Final result was {target}. Winner predicted {winner.prediction_answer} (off by {diff:.2f})."

    @staticmethod
    def _resolve_precision_timer(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.timer_delta_ms is not None]
        if not valid_entries:
            return None, "No timer attempts recorded."

        winner = min(valid_entries, key=lambda p: abs(p.timer_delta_ms))
        return winner, f"Winner achieved closest time with delta of {winner.timer_delta_ms} ms."
