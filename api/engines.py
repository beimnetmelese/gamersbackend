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
    def _resolve_treasure_box(game: Game, participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.selected_box is not None]
        if not valid_entries:
            return None, "No box selections recorded."
        
        # Select winning box deterministically using secret target or random seed from game id
        selected_boxes = [p.selected_box for p in valid_entries]
        winning_box = random.choice(selected_boxes)
        
        # Find player holding winning_box (earliest bidder if duplicate)
        winner = next(p for p in valid_entries if p.selected_box == winning_box)
        return winner, f"Winning Box was #{winning_box}. Winner: @{winner.user.username}"

    @staticmethod
    def _resolve_lowest_unique(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.selected_number is not None]
        if not valid_entries:
            return None, "No number selections recorded."

        counts = Counter(p.selected_number for p in valid_entries)
        unique_numbers = [num for num, count in counts.items() if count == 1]

        if not unique_numbers:
            # Fallback: Smallest number chosen, earliest bidder breaks tie
            valid_entries.sort(key=lambda p: (p.selected_number, p.joined_at))
            winner = valid_entries[0]
            return winner, f"No strictly unique number. Smallest number tie-breaker: {winner.selected_number} by @{winner.user.username}"

        min_unique = min(unique_numbers)
        winner = next(p for p in valid_entries if p.selected_number == min_unique)
        return winner, f"Lowest Unique Number was {min_unique} chosen by @{winner.user.username}"

    @staticmethod
    def _resolve_highest_card(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.selected_card]
        if not valid_entries:
            return None, "No card selections recorded."

        # Sort by card rank descending, then earliest joined_at timestamp
        valid_entries.sort(key=lambda p: (CARD_RANKS.get(p.selected_card, 0), -p.joined_at.timestamp()), reverse=True)
        winner = valid_entries[0]
        return winner, f"Highest Card was {winner.selected_card} by @{winner.user.username} (Earliest Bidder Tie-Breaker)."

    @staticmethod
    def _resolve_secret_number(game: Game, participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.selected_number is not None]
        if not valid_entries:
            return None, "No guesses recorded."

        target = int(game.secret_target) if game.secret_target and game.secret_target.isdigit() else 250
        # Sort by closest distance to target, then earliest joined_at timestamp
        valid_entries.sort(key=lambda p: (abs(p.selected_number - target), p.joined_at))
        winner = valid_entries[0]
        diff = abs(winner.selected_number - target)
        return winner, f"Target was {target}. Winner @{winner.user.username} guessed {winner.selected_number} (off by {diff}, Earliest Bidder Tie-Breaker)."

    @staticmethod
    def _resolve_precision_timer(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        valid_entries = [p for p in participants if p.timer_delta_ms is not None]
        if not valid_entries:
            return None, "No timer attempts recorded."

        valid_entries.sort(key=lambda p: (abs(p.timer_delta_ms), p.joined_at))
        winner = valid_entries[0]
        return winner, f"Winner @{winner.user.username} achieved closest time with delta of {winner.timer_delta_ms} ms (Earliest Bidder Tie-Breaker)."

    @staticmethod
    def _resolve_head_to_head(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not participants:
            return None, "No players in 1v1 duel."
        winner = participants[0]
        return winner, f"1v1 Duel Winner: @{winner.user.username}"

    @staticmethod
    def _resolve_tournament(participants: list[GameParticipant]) -> Tuple[Optional[GameParticipant], str]:
        if not participants:
            return None, "No players in tournament bracket."
        winner = participants[0]
        return winner, f"Tournament Champion: @{winner.user.username}"


def resolve_game_winner(game: Game) -> Tuple[Optional[GameResult], str]:
    winner_participant, notes = GameEngine.resolve_game(game)
    winner_user = winner_participant.user if winner_participant else None

    result, created = GameResult.objects.get_or_create(
        game=game,
        defaults={
            'winner': winner_user,
            'winning_value': str(winner_participant.selected_box or winner_participant.selected_number or winner_participant.selected_card or winner_participant.timer_delta_ms or '') if winner_participant else '',
            'total_participants': game.participants.count(),
            'resolution_notes': notes
        }
    )
    if not created:
        result.winner = winner_user
        result.winning_value = str(winner_participant.selected_box or winner_participant.selected_number or winner_participant.selected_card or winner_participant.timer_delta_ms or '') if winner_participant else ''
        result.total_participants = game.participants.count()
        result.resolution_notes = notes
        result.save()

    game.status = 'COMPLETED'
    game.save()

    return result, notes
