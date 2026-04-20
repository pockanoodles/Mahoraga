import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from backend.orchestrator.lru_cache import ThreadSafeLRUCache


class TestThreadSafeLRUCache:
    def test_basic_operations(self):
        cache = ThreadSafeLRUCache[str, int](capacity=3)
        
        # Test put and get
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.size() == 3

    def test_capacity_enforcement(self):
        cache = ThreadSafeLRUCache[str, int](capacity=2)
        
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # Should evict "a"
        
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
        assert cache.size() == 2

    def test_lru_eviction_order(self):
        cache = ThreadSafeLRUCache[str, int](capacity=3)
        
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        
        # Access "a" to make it most recently used
        cache.get("a")
        
        # Add new item, should evict "b" (least recently used)
        cache.put("d", 4)
        
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_update_existing_key(self):
        cache = ThreadSafeLRUCache[str, int](capacity=2)
        
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)  # Update existing key
        
        assert cache.get("a") == 10
        assert cache.get("b") == 2
        assert cache.size() == 2

    def test_get_nonexistent_key(self):
        cache = ThreadSafeLRUCache[str, int](capacity=2)
        
        assert cache.get("nonexistent") is None

    def test_contains(self):
        cache = ThreadSafeLRUCache[str, int](capacity=2)
        
        cache.put("a", 1)
        
        assert cache.contains("a") is True
        assert cache.contains("b") is False

    def test_clear(self):
        cache = ThreadSafeLRUCache[str, int](capacity=2)
        
        cache.put("a", 1)
        cache.put("b", 2)
        
        cache.clear()
        
        assert cache.size() == 0
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_capacity_property(self):
        cache = ThreadSafeLRUCache[str, int](capacity=5)
        assert cache.capacity() == 5

    def test_invalid_capacity(self):
        with pytest.raises(ValueError, match="Capacity must be positive"):
            ThreadSafeLRUCache[str, int](capacity=0)
        
        with pytest.raises(ValueError, match="Capacity must be positive"):
            ThreadSafeLRUCache[str, int](capacity=-1)

    def test_thread_safety_concurrent_puts(self):
        cache = ThreadSafeLRUCache[int, str](capacity=100)
        num_threads = 10
        items_per_thread = 50
        
        def worker(thread_id: int):
            for i in range(items_per_thread):
                key = thread_id * items_per_thread + i
                cache.put(key, f"value_{key}")
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()
        
        # Verify all items are present (within capacity)
        assert cache.size() == 100
        
        # Verify we can retrieve items
        retrieved_count = 0
        for i in range(num_threads * items_per_thread):
            if cache.get(i) is not None:
                retrieved_count += 1
        
        assert retrieved_count == 100

    def test_thread_safety_concurrent_gets_and_puts(self):
        cache = ThreadSafeLRUCache[int, str](capacity=50)
        num_threads = 8
        operations_per_thread = 100
        
        # Pre-populate cache
        for i in range(25):
            cache.put(i, f"initial_{i}")
        
        def worker(thread_id: int):
            for i in range(operations_per_thread):
                if i % 2 == 0:
                    # Put operation
                    key = thread_id * operations_per_thread + i
                    cache.put(key, f"thread_{thread_id}_value_{i}")
                else:
                    # Get operation
                    key = i % 25  # Try to get from initial values
                    cache.get(key)
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()
        
        # Cache should be at capacity
        assert cache.size() == 50

    def test_thread_safety_stress_test(self):
        cache = ThreadSafeLRUCache[str, int](capacity=20)
        num_threads = 20
        operations_per_thread = 200
        
        def worker(thread_id: int):
            for i in range(operations_per_thread):
                key = f"key_{i % 30}"  # Create some key overlap
                
                if i % 3 == 0:
                    cache.put(key, thread_id * 1000 + i)
                elif i % 3 == 1:
                    cache.get(key)
                else:
                    cache.contains(key)
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                future.result()
        
        # Verify cache is still in valid state
        assert cache.size() <= 20
        assert cache.capacity() == 20

    def test_lru_behavior_with_gets(self):
        cache = ThreadSafeLRUCache[str, int](capacity=3)
        
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        
        # Access "a" and "b" to make them more recently used than "c"
        cache.get("a")
        cache.get("b")
        
        # Add new item, should evict "c"
        cache.put("d", 4)
        
        assert cache.get("a") == 1
        assert cache.get("b") == 2
        assert cache.get("c") is None
        assert cache.get("d") == 4

    def test_type_safety(self):
        # Test with different types
        str_cache = ThreadSafeLRUCache[str, str](capacity=2)
        str_cache.put("key", "value")
        assert str_cache.get("key") == "value"
        
        int_cache = ThreadSafeLRUCache[int, list](capacity=2)
        int_cache.put(1, [1, 2, 3])
        assert int_cache.get(1) == [1, 2, 3]