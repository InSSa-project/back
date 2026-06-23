from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.users.jwt.service import JwtService
from apps.users.models import UserProfile

from .models import CommunityComment, CommunityPost, CommunityPostLike


@override_settings(SECURE_SSL_REDIRECT=False)
class CommunityApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='community-user',
            email='community-user@example.com',
            password='password',
            name='Community User',
        )
        self.other_user = User.objects.create_user(
            username='community-other',
            email='community-other@example.com',
            password='password',
            name='Other User',
        )
        UserProfile.objects.create(user=self.user, generation=14)
        UserProfile.objects.create(user=self.other_user, generation=13)

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {JwtService().issue_token(user, token_type='access')}"}

    def _create_post(self, author=None, board_type=CommunityPost.BOARD_GENERAL, title='Post', content='Content'):
        return CommunityPost.objects.create(
            author=author or self.user,
            board_type=board_type,
            title=title,
            content=content,
        )

    def test_authenticated_user_can_create_general_post(self):
        response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'Study group', 'content': 'Looking for members'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()['data']
        self.assertEqual(payload['board_type'], 'general')
        self.assertEqual(payload['author']['name'], 'Community User')
        self.assertEqual(payload['author']['generation'], 14)
        self.assertTrue(CommunityPost.objects.filter(author=self.user, title='Study group').exists())

    def test_authenticated_user_can_create_suggestion_post(self):
        response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'suggestion', 'title': 'Improve calendar', 'content': 'Please add filters'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['board_type'], 'suggestion')

    def test_unauthenticated_user_cannot_create_post(self):
        response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'No auth', 'content': 'Blocked'},
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)

    def test_post_list_filters_by_board_type_and_paginates(self):
        general_post = self._create_post(title='General')
        self._create_post(board_type=CommunityPost.BOARD_SUGGESTION, title='Suggestion')

        response = self.client.get(
            reverse('community-post-list'),
            {'board_type': 'general', 'page': 1},
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        page = response.json()['data']
        self.assertEqual(page['count'], 1)
        self.assertEqual([item['id'] for item in page['results']], [general_post.id])

    def test_post_author_payload_handles_missing_profile(self):
        no_profile_user = get_user_model().objects.create_user(
            username='no-profile',
            email='no-profile@example.com',
            password='password',
            name='No Profile',
        )
        post = self._create_post(author=no_profile_user)

        response = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['author']['name'], 'No Profile')
        self.assertIsNone(response.json()['data']['author']['generation'])

    def test_only_owner_can_update_and_delete_post(self):
        post = self._create_post()

        blocked_patch = self.client.patch(
            reverse('community-post-detail', args=[post.id]),
            data={'title': 'Blocked'},
            content_type='application/json',
            **self._auth(self.other_user),
        )
        owner_patch = self.client.patch(
            reverse('community-post-detail', args=[post.id]),
            data={'title': 'Updated'},
            content_type='application/json',
            **self._auth(self.user),
        )
        blocked_delete = self.client.delete(reverse('community-post-detail', args=[post.id]), **self._auth(self.other_user))
        owner_delete = self.client.delete(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))

        self.assertEqual(blocked_patch.status_code, 403)
        self.assertEqual(owner_patch.status_code, 200)
        self.assertEqual(owner_patch.json()['data']['title'], 'Updated')
        self.assertEqual(blocked_delete.status_code, 403)
        self.assertEqual(owner_delete.status_code, 204)
        self.assertFalse(CommunityPost.objects.filter(pk=post.id).exists())

    def test_staff_can_delete_other_users_post(self):
        staff = get_user_model().objects.create_user(
            username='community-staff',
            email='community-staff@example.com',
            password='password',
            is_staff=True,
        )
        post = self._create_post()

        response = self.client.delete(reverse('community-post-detail', args=[post.id]), **self._auth(staff))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CommunityPost.objects.filter(pk=post.id).exists())

    def test_like_create_is_idempotent_and_unlike_is_safe(self):
        post = self._create_post()

        first_like = self.client.post(reverse('community-post-like', args=[post.id]), **self._auth(self.user))
        second_like = self.client.post(reverse('community-post-like', args=[post.id]), **self._auth(self.user))
        unlike = self.client.delete(reverse('community-post-like', args=[post.id]), **self._auth(self.user))
        second_unlike = self.client.delete(reverse('community-post-like', args=[post.id]), **self._auth(self.user))

        self.assertEqual(first_like.status_code, 200)
        self.assertEqual(second_like.status_code, 200)
        self.assertEqual(CommunityPostLike.objects.filter(post=post, user=self.user).count(), 0)
        self.assertEqual(first_like.json()['data']['like_count'], 1)
        self.assertEqual(second_like.json()['data']['like_count'], 1)
        self.assertEqual(unlike.json()['data']['is_liked'], False)
        self.assertEqual(second_unlike.json()['data']['like_count'], 0)

    def test_is_liked_is_per_user_and_missing_post_like_returns_404(self):
        post = self._create_post()
        CommunityPostLike.objects.create(post=post, user=self.user)

        liked_response = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))
        other_response = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.other_user))
        missing_response = self.client.post(reverse('community-post-like', args=[999999]), **self._auth(self.user))

        self.assertTrue(liked_response.json()['data']['is_liked'])
        self.assertFalse(other_response.json()['data']['is_liked'])
        self.assertEqual(liked_response.json()['data']['like_count'], 1)
        self.assertEqual(missing_response.status_code, 404)

    def test_comments_support_replies_and_nested_replies(self):
        post = self._create_post()
        root = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Root comment', 'parent_id': None},
            content_type='application/json',
            **self._auth(self.user),
        )
        reply = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Reply', 'parent_id': root.json()['data']['id']},
            content_type='application/json',
            **self._auth(self.other_user),
        )
        nested = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Nested reply', 'parent_id': reply.json()['data']['id']},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(root.status_code, 201)
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(nested.status_code, 201)
        self.assertEqual(nested.json()['data']['parent_id'], reply.json()['data']['id'])

    def test_comment_parent_must_belong_to_same_post(self):
        post = self._create_post(title='One')
        other_post = self._create_post(title='Two')
        other_comment = CommunityComment.objects.create(post=other_post, author=self.user, content='Other')

        response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Wrong parent', 'parent_id': other_comment.id},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('parent_id', response.json())

    def test_only_owner_can_update_comment_and_deleted_comment_cannot_be_updated(self):
        post = self._create_post()
        comment = CommunityComment.objects.create(post=post, author=self.user, content='Original')

        blocked = self.client.patch(
            reverse('community-comment-detail', args=[comment.id]),
            data={'content': 'Blocked'},
            content_type='application/json',
            **self._auth(self.other_user),
        )
        updated = self.client.patch(
            reverse('community-comment-detail', args=[comment.id]),
            data={'content': 'Updated'},
            content_type='application/json',
            **self._auth(self.user),
        )
        self.client.delete(reverse('community-comment-detail', args=[comment.id]), **self._auth(self.user))
        deleted_update = self.client.patch(
            reverse('community-comment-detail', args=[comment.id]),
            data={'content': 'Again'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['data']['content'], 'Updated')
        self.assertEqual(deleted_update.status_code, 400)

    def test_comment_delete_soft_deletes_and_keeps_child_replies(self):
        post = self._create_post()
        root = CommunityComment.objects.create(post=post, author=self.user, content='Root')
        reply = CommunityComment.objects.create(post=post, author=self.other_user, parent=root, content='Reply')

        response = self.client.delete(reverse('community-comment-detail', args=[root.id]), **self._auth(self.user))
        list_response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))
        detail_response = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 204)
        root.refresh_from_db()
        self.assertTrue(root.is_deleted)
        self.assertTrue(CommunityComment.objects.filter(pk=reply.id).exists())
        payload = list_response.json()['data']
        self.assertEqual(payload[0]['content'], 'Deleted comment.')
        self.assertIsNone(payload[0]['author'])
        self.assertEqual(payload[1]['parent_id'], root.id)
        self.assertEqual(detail_response.json()['data']['comment_count'], 1)

    def test_invalid_input_and_missing_resources_return_expected_statuses(self):
        post = self._create_post()

        invalid_post = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'wrong', 'title': ' ', 'content': ' '},
            content_type='application/json',
            **self._auth(self.user),
        )
        missing_post = self.client.get(reverse('community-post-detail', args=[999999]), **self._auth(self.user))
        missing_comment = self.client.delete(reverse('community-comment-detail', args=[999999]), **self._auth(self.user))
        blank_comment = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': '   '},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(invalid_post.status_code, 400)
        self.assertEqual(missing_post.status_code, 404)
        self.assertEqual(missing_comment.status_code, 404)
        self.assertEqual(blank_comment.status_code, 400)
