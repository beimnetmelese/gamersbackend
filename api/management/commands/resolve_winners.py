import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from api.models import Game, GameParticipant, GameResult
from api.engines import GameEngine

class Command(BaseCommand):
    help = "Determine game winners backend engine script and persist results to DB"

    def add_arguments(self, parser):
        parser.add_argument(
            '--game-id',
            type=int,
            help='Specific Game ID to resolve. If omitted, resolves all active games.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force resolve active games even if bidding timer has not ended yet.',
        )
        parser.add_argument(
            '--seed-participants',
            action='store_true',
            help='Automatically seed sample participant entries for active games before resolving.',
        )

    def handle(self, *args, **options):
        game_id = options.get('game_id')
        should_seed = options.get('seed_participants', True)
        force_mode = options.get('force', False)

        if game_id:
            games = Game.objects.filter(id=game_id)
            if not games.exists():
                self.stdout.write(self.style.ERROR(f"Game #{game_id} not found."))
                return
        else:
            games = Game.objects.filter(status='ACTIVE')

        if not games.exists():
            self.stdout.write(self.style.WARNING("No active games found to resolve."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\n" + "="*75))
        self.stdout.write(self.style.MIGRATE_HEADING(" *** ALL-IN BACKEND ENGINE: WINNER RESOLUTION PROCESS *** "))
        self.stdout.write(self.style.MIGRATE_HEADING("="*75 + "\n"))

        # Sample test users for seeding if participants are missing
        resolved_count = 0

        for game in games:
            # Skip games whose bidding timer has NOT ended yet
            if not game.is_bidding_ended() and not force_mode:
                self.stdout.write(self.style.WARNING(f"[SKIP] Skipping Game #{game.id} [{game.title}]: Bidding timer has NOT ended yet."))
                continue

            participants = list(game.participants.all())
            if not participants:
                # Resolve as NO WINNER if 0 participants submitted bids
                GameResult.objects.update_or_create(
                    game=game,
                    defaults={
                        'winner': None,
                        'winning_value': 'No Bids Submitted',
                        'total_participants': 0,
                        'resolution_notes': 'Bidding timer ended with 0 participants. Game closed with No Winner.',
                        'calculated_at': timezone.now()
                    }
                )
                game.status = 'COMPLETED'
                game.save()
                resolved_count += 1
                self.stdout.write(self.style.NOTICE(f"[NO WINNER] Game #{game.id} [{game.title}] ended with 0 bids. Marked COMPLETED with No Winner."))
                continue

            # Evaluate Winner using Engine
            winner_participant, details = GameEngine.resolve_game(game)

            if not winner_participant:
                self.stdout.write(self.style.ERROR(f"X Game #{game.id} [{game.title}]: Could not resolve winner. Reason: {details}"))
                continue

            winner_user = winner_participant.user
            
            # Format winning value string
            if game.game_type == 'TREASURE_BOX':
                winning_val = f"Box #{winner_participant.selected_box}"
            elif game.game_type == 'LOWEST_UNIQUE':
                winning_val = f"Number {winner_participant.selected_number}"
            elif game.game_type == 'HIGHEST_CARD':
                winning_val = f"Card {winner_participant.selected_card}"
            elif game.game_type == 'SECRET_NUMBER':
                winning_val = f"Guess {winner_participant.selected_number} (Secret: {game.secret_target})"
            elif game.game_type == 'PRECISION_TIMER':
                winning_val = f"Delta +{winner_participant.timer_delta_ms} ms (Target: {game.target_time_sec}s)"
            else:
                winning_val = f"Score Value"

            # Create or update GameResult
            game_result, created = GameResult.objects.update_or_create(
                game=game,
                defaults={
                    'winner': winner_user,
                    'winning_value': winning_val,
                    'total_participants': len(participants),
                    'resolution_notes': details,
                    'calculated_at': timezone.now()
                }
            )

            # Mark game COMPLETED
            game.status = 'COMPLETED'
            game.save()

            resolved_count += 1

            self.stdout.write(self.style.SUCCESS(f"[+] GAME #{game.id} RESOLVED SUCCESSFULLY!"))
            self.stdout.write(f"  * Challenge Title : {game.title}")
            self.stdout.write(f"  * Engine Type     : {game.get_game_type_display()}")
            self.stdout.write(f"  * Winner User     : @{winner_user.username}")
            self.stdout.write(f"  * Winning Value   : {winning_val}")
            self.stdout.write(f"  * Product Won     : {game.product.title} (Val: {game.product.estimated_value} ETB)")
            self.stdout.write(f"  * Details         : {details}")
            self.stdout.write(self.style.HTTP_INFO("-" * 75))

        self.stdout.write(self.style.SUCCESS(f"\n[OK] Finished! Resolved {resolved_count} games in total.\n"))
