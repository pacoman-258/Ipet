from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from backend.chat_topics import TopicStore
from backend.ipet_memory_store import IpetMemoryStore


class ConcurrencySafetyTests(unittest.TestCase):
    def test_topic_store_concurrent_read_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicStore(Path(temp_dir))
            topic_meta = store.create_topic(title="并发测试话题")
            topic_id = topic_meta["topic_id"]

            errors: list[Exception] = []
            stop_event = threading.Event()

            def writer() -> None:
                try:
                    for index in range(50):
                        store.append_exchange(
                            topic_id,
                            user_text=f"用户消息 {index}",
                            assistant_text=f"助手回答 {index}",
                        )
                except Exception as exc:
                    errors.append(exc)
                finally:
                    stop_event.set()

            def reader() -> None:
                while not stop_event.is_set():
                    try:
                        store.load_meta(topic_id)
                        store.load_summary_document(topic_id)
                        store.load_full_messages(topic_id)
                        store.list_topics()
                    except Exception as exc:
                        errors.append(exc)

            writer_thread = threading.Thread(target=writer)
            reader_thread = threading.Thread(target=reader)

            writer_thread.start()
            reader_thread.start()

            writer_thread.join()
            reader_thread.join()

            self.assertEqual(errors, [])
            final_meta = store.load_meta(topic_id)
            self.assertIsNotNone(final_meta)
            self.assertEqual(final_meta["assistant_turn_count"], 50)

    def test_ipet_memory_store_concurrent_read_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = IpetMemoryStore(Path(temp_dir))

            errors: list[Exception] = []
            stop_event = threading.Event()

            def writer() -> None:
                try:
                    for index in range(30):
                        store.write_memory(
                            title=f"测试记忆_{index}",
                            summary=f"记住用户偏好事项编号_{index}",
                            kind="preference",
                        )
                except Exception as exc:
                    errors.append(exc)
                finally:
                    stop_event.set()

            def reader() -> None:
                while not stop_event.is_set():
                    try:
                        store.list_memories()
                        store.search("偏好")
                    except Exception as exc:
                        errors.append(exc)

            writer_thread = threading.Thread(target=writer)
            reader_thread = threading.Thread(target=reader)

            writer_thread.start()
            reader_thread.start()

            writer_thread.join()
            reader_thread.join()

            self.assertEqual(errors, [])
            memories = store.list_memories()
            self.assertEqual(len(memories), 30)


if __name__ == "__main__":
    unittest.main()
