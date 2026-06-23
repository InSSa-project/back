from django.test import SimpleTestCase

from ai_server.rag.schemas.documents import RetrievedChunk
from ai_server.references.tracker import ReferenceTracker
from apps.ai.services import FastAPIAIClient


class ReferenceUrlTests(SimpleTestCase):
    def test_reference_tracker_exposes_source_urls(self):
        chunk = RetrievedChunk(
            chunk_id='doc:1',
            ai_document_id=7,
            raw_data_id=3,
            title='SSAFY notice',
            content='Grounded content',
            document_type='SYNC_NOTICE',
            metadata={
                'source_type': 'notice',
                'source_url': 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1',
                'detail_url': 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1',
            },
            score=0.9,
        )

        reference = ReferenceTracker().from_chunks([chunk])[0]

        self.assertEqual(reference.source_type, 'notice')
        self.assertEqual(reference.source_url, 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1')
        self.assertEqual(reference.detail_url, 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1')

    def test_django_ai_client_keeps_reference_urls(self):
        reference = FastAPIAIClient()._normalize_reference(
            {
                'ai_document_id': 7,
                'raw_data_id': 3,
                'title': 'SSAFY notice',
                'source_type': 'notice',
                'score': 0.9,
                'chunk_id': 'doc:1',
                'snippet': 'Grounded content',
                'metadata': {
                    'source_url': 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1',
                    'detail_url': 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1',
                },
            }
        )

        self.assertEqual(reference['document_id'], 7)
        self.assertEqual(reference['ai_document_id'], 7)
        self.assertEqual(reference['source_url'], 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1')
        self.assertEqual(reference['detail_url'], 'https://edu.ssafy.com/edu/board/notice/detail.do?id=1')
        self.assertIn('metadata', reference)
