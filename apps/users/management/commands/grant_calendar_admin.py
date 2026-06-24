from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = 'Grant calendar admin permission by setting is_staff=True for a user.'

    def add_arguments(self, parser):
        parser.add_argument('identifier', help='User email, username, Mattermost user id, or Mattermost username.')

    def handle(self, *args, **options):
        identifier = str(options['identifier']).strip()
        if not identifier:
            raise CommandError('identifier is required.')

        User = get_user_model()
        users = User.objects.filter(
            Q(email=identifier)
            | Q(username=identifier)
            | Q(profile__mattermost_user_id=identifier)
            | Q(profile__mattermost_username=identifier)
        ).distinct()

        count = users.count()
        if count == 0:
            raise CommandError(f'User not found: {identifier}')
        if count > 1:
            raise CommandError(f'Multiple users matched: {identifier}')

        user = users.get()
        if user.is_staff:
            self.stdout.write(self.style.WARNING(f'User already has calendar admin permission: {user.email}'))
            return

        user.is_staff = True
        user.save(update_fields=['is_staff'])
        self.stdout.write(self.style.SUCCESS(f'Granted calendar admin permission: {user.email}'))
