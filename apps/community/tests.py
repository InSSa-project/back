import concurrent.futures

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.users.jwt.service import JwtService
from apps.users.models import UserProfile

from .models import CommunityComment, CommunityPost, CommunityPostLike
from .serializers import COMMENT_CONTENT_MAX_LENGTH, POST_CONTENT_MAX_LENGTH, POST_TITLE_MAX_LENGTH


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

    def _assert_post_detail_contract(self, payload):
        expected_fields = {
            'id',
            'board_type',
            'title',
            'content',
            'author',
            'like_count',
            'comment_count',
            'is_liked',
            'is_owner',
            'is_edited',
            'edited_at',
            'created_at',
            'updated_at',
        }
        self.assertTrue(expected_fields.issubset(payload.keys()))
        self.assertIsInstance(payload['id'], int)
        self.assertIsInstance(payload['like_count'], int)
        self.assertIsInstance(payload['comment_count'], int)
        self.assertIsInstance(payload['is_liked'], bool)
        self.assertIsInstance(payload['is_owner'], bool)
        self.assertIsInstance(payload['is_edited'], bool)
        self.assertIn('id', payload['author'])
        self.assertIn('name', payload['author'])
        self.assertIn('generation', payload['author'])
        self.assertIn('profile_image_url', payload['author'])

    def _assert_post_list_contract(self, payload):
        expected_fields = {
            'id',
            'board_type',
            'title',
            'content_preview',
            'author',
            'like_count',
            'comment_count',
            'is_liked',
            'is_owner',
            'is_edited',
            'edited_at',
            'created_at',
            'updated_at',
        }
        self.assertTrue(expected_fields.issubset(payload.keys()))
        self.assertIsInstance(payload['is_edited'], bool)

    def test_authenticated_user_can_create_general_post(self):
        response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'Study group', 'content': 'Looking for members'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(response.json()['message'], 'Community post created.')
        payload = response.json()['data']
        self._assert_post_detail_contract(payload)
        self.assertIn('id', payload)
        self.assertEqual(payload['board_type'], 'general')
        self.assertEqual(payload['content'], 'Looking for members')
        self.assertEqual(payload['author']['name'], 'Community User')
        self.assertEqual(payload['author']['generation'], 14)
        self.assertFalse(payload['is_edited'])
        self.assertIsNone(payload['edited_at'])
        self.assertTrue(CommunityPost.objects.filter(author=self.user, title='Study group').exists())

        list_response = self.client.get(reverse('community-post-list'), **self._auth(self.user))
        detail_response = self.client.get(reverse('community-post-detail', args=[payload['id']]), **self._auth(self.user))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(list_response.json()['data']['results'][0]['id'], payload['id'])
        self._assert_post_list_contract(list_response.json()['data']['results'][0])
        self._assert_post_detail_contract(detail_response.json()['data'])

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
        self.assertEqual(owner_patch.json()['message'], 'Community post updated.')
        payload = owner_patch.json()['data']
        self._assert_post_detail_contract(payload)
        self.assertEqual(payload['title'], 'Updated')
        self.assertEqual(payload['content'], 'Content')
        self.assertTrue(payload['is_edited'])
        self.assertIsNotNone(payload['edited_at'])
        self.assertEqual(blocked_delete.status_code, 403)
        self.assertEqual(owner_delete.status_code, 204)
        self.assertFalse(CommunityPost.objects.filter(pk=post.id).exists())

    def test_post_update_response_contains_changed_title_and_content(self):
        post = self._create_post(title='Before title', content='Before content')

        response = self.client.patch(
            reverse('community-post-detail', args=[post.id]),
            data={'title': 'After title', 'content': 'After content'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self._assert_post_detail_contract(payload)
        self.assertEqual(payload['id'], post.id)
        self.assertEqual(payload['title'], 'After title')
        self.assertEqual(payload['content'], 'After content')
        self.assertTrue(payload['is_edited'])
        self.assertIsNotNone(payload['edited_at'])

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

    def test_like_and_comment_do_not_change_post_edited_at(self):
        post = self._create_post()
        patch_response = self.client.patch(
            reverse('community-post-detail', args=[post.id]),
            data={'title': 'Edited once'},
            content_type='application/json',
            **self._auth(self.user),
        )
        self.assertEqual(patch_response.status_code, 200)
        post.refresh_from_db()
        edited_at = post.edited_at

        like_response = self.client.post(reverse('community-post-like', args=[post.id]), **self._auth(self.other_user))
        comment_response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'No post edit'},
            content_type='application/json',
            **self._auth(self.other_user),
        )

        self.assertEqual(like_response.status_code, 200)
        self.assertEqual(comment_response.status_code, 201)
        post.refresh_from_db()
        self.assertEqual(post.edited_at, edited_at)

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
        self.assertFalse(payload[0]['is_owner'])
        self.assertEqual(payload[1]['parent_id'], root.id)
        self.assertEqual(detail_response.json()['data']['comment_count'], 1)

    def test_comment_list_returns_empty_array_for_existing_post_without_comments(self):
        post = self._create_post()

        response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(response.json()['data'], [])
        self.assertEqual(response.json()['message'], 'OK')

    def test_comment_list_returns_404_only_when_post_does_not_exist(self):
        response = self.client.get(reverse('community-comment-list', args=[999999]), **self._auth(self.user))

        self.assertEqual(response.status_code, 404)

    def test_new_post_comment_list_is_available_immediately_without_refresh(self):
        create_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'Fresh post', 'content': 'Fresh content'},
            content_type='application/json',
            **self._auth(self.user),
        )

        response = self.client.get(
            reverse('community-comment-list', args=[create_response.json()['data']['id']]),
            **self._auth(self.user),
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], [])

    def test_new_comment_is_available_immediately_without_refresh(self):
        create_post_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'Commentable post', 'content': 'Commentable content'},
            content_type='application/json',
            **self._auth(self.user),
        )
        post_id = create_post_response.json()['data']['id']

        create_comment_response = self.client.post(
            reverse('community-comment-list', args=[post_id]),
            data={'content': 'Immediate comment'},
            content_type='application/json',
            **self._auth(self.user),
        )
        list_response = self.client.get(reverse('community-comment-list', args=[post_id]), **self._auth(self.user))

        self.assertEqual(create_comment_response.status_code, 201)
        self.assertIn('id', create_comment_response.json()['data'])
        self.assertEqual(create_comment_response.json()['data']['author']['name'], 'Community User')
        self.assertTrue(create_comment_response.json()['data']['is_owner'])
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([item['id'] for item in list_response.json()['data']], [create_comment_response.json()['data']['id']])

    def test_existing_comment_lists_are_stable_on_first_and_repeated_requests(self):
        empty_post = self._create_post(title='Empty existing')
        comment_post = self._create_post(title='Commented existing')
        comment = CommunityComment.objects.create(post=comment_post, author=self.user, content='Existing comment')

        empty_responses = [
            self.client.get(reverse('community-comment-list', args=[empty_post.id]), **self._auth(self.user))
            for _ in range(3)
        ]
        comment_responses = [
            self.client.get(reverse('community-comment-list', args=[comment_post.id]), **self._auth(self.user))
            for _ in range(3)
        ]

        for response in empty_responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['data'], [])
        for response in comment_responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.json()['data']], [comment.id])

    def test_comment_list_handles_author_without_profile(self):
        no_profile_user = get_user_model().objects.create_user(
            username='comment-no-profile',
            email='comment-no-profile@example.com',
            password='password',
            name='No Profile Commenter',
        )
        post = self._create_post(author=no_profile_user)
        comment = CommunityComment.objects.create(post=post, author=no_profile_user, content='No profile comment')

        response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data'][0]
        self.assertEqual(payload['id'], comment.id)
        self.assertEqual(payload['author']['name'], 'No Profile Commenter')
        self.assertIsNone(payload['author']['generation'])

    def test_deleted_comment_contract_for_author_null_and_replies(self):
        post = self._create_post()
        root = CommunityComment.objects.create(post=post, author=self.user, content='Root', is_deleted=True)
        reply = CommunityComment.objects.create(post=post, author=self.other_user, parent=root, content='Reply')

        response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        payload = response.json()['data']
        self.assertEqual(payload[0]['id'], root.id)
        self.assertIsNone(payload[0]['author'])
        self.assertEqual(payload[0]['content'], 'Deleted comment.')
        self.assertTrue(payload[0]['is_deleted'])
        self.assertFalse(payload[0]['is_owner'])
        self.assertEqual(payload[1]['id'], reply.id)
        self.assertEqual(payload[1]['parent_id'], root.id)

    def test_post_title_length_limits(self):
        valid_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 't' * POST_TITLE_MAX_LENGTH, 'content': 'content'},
            content_type='application/json',
            **self._auth(self.user),
        )
        invalid_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 't' * (POST_TITLE_MAX_LENGTH + 1), 'content': 'content'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(valid_response.status_code, 201)
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn('title', invalid_response.json())

    def test_post_content_length_limits(self):
        valid_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'Valid content length', 'content': 'c' * POST_CONTENT_MAX_LENGTH},
            content_type='application/json',
            **self._auth(self.user),
        )
        invalid_response = self.client.post(
            reverse('community-post-list'),
            data={
                'board_type': 'general',
                'title': 'Invalid content length',
                'content': 'c' * (POST_CONTENT_MAX_LENGTH + 1),
            },
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(valid_response.status_code, 201)
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn('content', invalid_response.json())

    def test_comment_content_length_limits(self):
        post = self._create_post()

        valid_response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'c' * COMMENT_CONTENT_MAX_LENGTH},
            content_type='application/json',
            **self._auth(self.user),
        )
        invalid_response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'c' * (COMMENT_CONTENT_MAX_LENGTH + 1)},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(valid_response.status_code, 201)
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn('content', invalid_response.json())

    def test_reply_content_length_limit_and_failed_create_does_not_leave_row(self):
        post = self._create_post()
        parent = CommunityComment.objects.create(post=post, author=self.user, content='Parent')

        response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'r' * (COMMENT_CONTENT_MAX_LENGTH + 1), 'parent_id': parent.id},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('content', response.json())
        self.assertEqual(CommunityComment.objects.filter(post=post).count(), 1)

    def test_whitespace_only_inputs_are_rejected(self):
        post = self._create_post()

        title_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': '   ', 'content': 'content'},
            content_type='application/json',
            **self._auth(self.user),
        )
        content_response = self.client.post(
            reverse('community-post-list'),
            data={'board_type': 'general', 'title': 'title', 'content': '   '},
            content_type='application/json',
            **self._auth(self.user),
        )
        comment_response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': '   '},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(title_response.status_code, 400)
        self.assertEqual(content_response.status_code, 400)
        self.assertEqual(comment_response.status_code, 400)
        self.assertIn('title', title_response.json())
        self.assertIn('content', content_response.json())
        self.assertIn('content', comment_response.json())

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

    def test_invalid_string_post_id_does_not_match_url_pattern(self):
        from django.urls import resolve, Resolver404
        for path in [
            '/api/v1/community/posts/undefined/',
            '/api/v1/community/posts/undefined/comments/',
            '/api/v1/community/posts/NaN/',
            '/api/v1/community/posts/NaN/comments/',
        ]:
            with self.assertRaises(Resolver404, msg=f"{path} unexpectedly resolved"):
                resolve(path)

    def test_zero_post_id_returns_404_not_500(self):
        for path in ['/api/v1/community/posts/0/', '/api/v1/community/posts/0/comments/']:
            response = self.client.get(path, **self._auth(self.user))
            self.assertNotEqual(response.status_code, 500, f"{path} returned 500")
            self.assertEqual(response.status_code, 404)

    def test_success_response_does_not_convert_empty_list_to_dict(self):
        post = self._create_post()

        response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'success')
        self.assertIsInstance(body['data'], list)
        self.assertEqual(body['data'], [])
        self.assertEqual(body['message'], 'OK')

    def test_post_detail_and_comment_list_are_both_200_in_sequence(self):
        post = self._create_post()
        CommunityComment.objects.create(post=post, author=self.user, content='Hello')

        detail = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))
        comments = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(comments.status_code, 200)
        self._assert_post_detail_contract(detail.json()['data'])
        self.assertEqual(len(comments.json()['data']), 1)

    def test_comment_post_response_contains_all_required_fields(self):
        post = self._create_post()

        response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'New comment'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()['data']
        for field in ['id', 'post_id', 'parent_id', 'author', 'content', 'is_deleted', 'is_owner', 'created_at', 'updated_at']:
            self.assertIn(field, data, f"Missing field: {field}")
        self.assertIsInstance(data['id'], int)
        self.assertEqual(data['post_id'], post.id)
        self.assertIsNone(data['parent_id'])
        self.assertFalse(data['is_deleted'])
        self.assertTrue(data['is_owner'])

    def test_comment_post_db_row_matches_response_id(self):
        post = self._create_post()

        response = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Check DB'},
            content_type='application/json',
            **self._auth(self.user),
        )

        self.assertEqual(response.status_code, 201)
        returned_id = response.json()['data']['id']
        self.assertTrue(CommunityComment.objects.filter(pk=returned_id, post=post).exists())

    def test_comment_post_immediately_visible_in_list(self):
        post = self._create_post()

        create = self.client.post(
            reverse('community-comment-list', args=[post.id]),
            data={'content': 'Immediate'},
            content_type='application/json',
            **self._auth(self.user),
        )
        list_response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(create.status_code, 201)
        self.assertEqual(list_response.status_code, 200)
        ids = [c['id'] for c in list_response.json()['data']]
        self.assertIn(create.json()['data']['id'], ids)

    def test_deleted_comment_included_in_list_with_masked_content(self):
        post = self._create_post()
        deleted = CommunityComment.objects.create(post=post, author=self.user, content='Will be deleted', is_deleted=True)
        live = CommunityComment.objects.create(post=post, author=self.user, content='Alive')

        response = self.client.get(reverse('community-comment-list', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(len(data), 2)
        deleted_item = next(c for c in data if c['id'] == deleted.id)
        self.assertTrue(deleted_item['is_deleted'])
        self.assertIsNone(deleted_item['author'])
        self.assertEqual(deleted_item['content'], 'Deleted comment.')

    def test_migration_edited_at_field_present_in_post_detail(self):
        post = self._create_post()

        response = self.client.get(reverse('community-post-detail', args=[post.id]), **self._auth(self.user))

        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertIn('edited_at', data)
        self.assertIn('is_edited', data)
        self.assertIsNone(data['edited_at'])
        self.assertFalse(data['is_edited'])


@override_settings(
    SECURE_SSL_REDIRECT=False,
    JWT_ACCESS_SLIDING_EXPIRATION=True,
    JWT_ACCESS_LIFETIME_SECONDS=60,
    JWT_ACCESS_MAX_LIFETIME_SECONDS=3600,
)
class JwtSlidingSessionConcurrencyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username='jwt-concurrency',
            email='jwt-concurrency@example.com',
            password='password',
            name='JWT User',
        )

    def test_repeated_jwt_verification_updates_session_without_error(self):
        from apps.users.models import JwtSession
        token = JwtService().issue_token(self.user, token_type='access')

        for _ in range(5):
            payload = JwtService().verify(token, expected_type='access')
            self.assertEqual(payload['user_id'], self.user.id)

        session = JwtSession.objects.get(user=self.user, token_type='access')
        self.assertIsNone(session.revoked_at)

    def test_jwt_verify_uses_update_not_select_for_update(self):
        from apps.users.models import JwtSession
        token = JwtService().issue_token(self.user, token_type='access')
        session_before = JwtSession.objects.get(user=self.user, token_type='access')
        idle_before = session_before.idle_expires_at

        JwtService().verify(token, expected_type='access')

        session_after = JwtSession.objects.get(user=self.user, token_type='access')
        self.assertGreaterEqual(session_after.idle_expires_at, idle_before)
        self.assertIsNone(session_after.revoked_at)
