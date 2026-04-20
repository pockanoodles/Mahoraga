#!/usr/bin/env python3
"""
Example usage of the ThreadSafeLRUCache class.
"""

from backend.orchestrator.lru_cache import ThreadSafeLRUCache
import threading
import time


def basic_usage_example():
    """Demonstrate basic LRU cache operations."""
    print("=== Basic Usage Example ===")
    
    # Create a cache with capacity of 3
    cache = ThreadSafeLRUCache[str, int](capacity=3)
    
    # Add some items
    cache.put("apple", 100)
    cache.put("banana", 200)
    cache.put("cherry", 300)
    
    print(f"Cache size: {cache.size()}")
    print(f"Get 'apple': {cache.get('apple')}")
    print(f"Get 'banana': {cache.get('banana')}")
    
    # Add another item - this will evict the least recently used
    cache.put("date", 400)
    
    print(f"After adding 'date', 'cherry' is evicted: {cache.get('cherry')}")
    print(f"'date' is present: {cache.get('date')}")
    print()


def thread_safety_example():
    """Demonstrate thread-safe operations."""
    print("=== Thread Safety Example ===")
    
    cache = ThreadSafeLRUCache[int, str](capacity=10)
    results = []
    
    def worker(thread_id: int):
        """Worker function that performs cache operations."""
        for i in range(5):
            key = thread_id * 10 + i
            value = f"thread_{thread_id}_item_{i}"
            
            # Put item in cache
            cache.put(key, value)
            
            # Try to get it back
            retrieved = cache.get(key)
            results.append((thread_id, key, retrieved == value))
            
            time.sleep(0.001)  # Small delay to increase contention
    
    # Create and start multiple threads
    threads = []
    for i in range(3):
        thread = threading.Thread(target=worker, args=(i,))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    # Check results
    successful_operations = sum(1 for _, _, success in results if success)
    print(f"Successful operations: {successful_operations}/{len(results)}")
    print(f"Final cache size: {cache.size()}")
    print()


def lru_behavior_example():
    """Demonstrate LRU eviction behavior."""
    print("=== LRU Behavior Example ===")
    
    cache = ThreadSafeLRUCache[str, str](capacity=3)
    
    # Fill the cache
    cache.put("first", "1st")
    cache.put("second", "2nd") 
    cache.put("third", "3rd")
    
    print("Initial cache contents:")
    for key in ["first", "second", "third"]:
        print(f"  {key}: {cache.get(key)}")
    
    # Access "first" to make it most recently used
    cache.get("first")
    print("\nAfter accessing 'first'...")
    
    # Add new item - should evict "second" (least recently used)
    cache.put("fourth", "4th")
    
    print("After adding 'fourth':")
    for key in ["first", "second", "third", "fourth"]:
        value = cache.get(key)
        status = "present" if value else "evicted"
        print(f"  {key}: {status}")
    print()


if __name__ == "__main__":
    basic_usage_example()
    thread_safety_example()
    lru_behavior_example()