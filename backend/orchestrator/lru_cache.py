from __future__ import annotations
import threading
from typing import Generic, TypeVar, Optional, Dict, Any
from dataclasses import dataclass

K = TypeVar('K')
V = TypeVar('V')


@dataclass
class _Node(Generic[K, V]):
    key: K
    value: V
    prev: Optional[_Node[K, V]] = None
    next: Optional[_Node[K, V]] = None


class ThreadSafeLRUCache(Generic[K, V]):
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        
        self._capacity = capacity
        self._cache: Dict[K, _Node[K, V]] = {}
        self._lock = threading.RLock()
        
        # Create dummy head and tail nodes for the doubly linked list
        self._head: _Node[K, V] = _Node(None, None)  # type: ignore
        self._tail: _Node[K, V] = _Node(None, None)  # type: ignore
        self._head.next = self._tail
        self._tail.prev = self._head

    def get(self, key: K) -> Optional[V]:
        with self._lock:
            if key not in self._cache:
                return None
            
            node = self._cache[key]
            # Move to front (most recently used)
            self._move_to_front(node)
            return node.value

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._cache:
                # Update existing key
                node = self._cache[key]
                node.value = value
                self._move_to_front(node)
            else:
                # Add new key
                if len(self._cache) >= self._capacity:
                    # Remove least recently used (tail)
                    self._remove_lru()
                
                # Create new node and add to front
                new_node = _Node(key, value)
                self._cache[key] = new_node
                self._add_to_front(new_node)

    def _move_to_front(self, node: _Node[K, V]) -> None:
        # Remove from current position
        self._remove_node(node)
        # Add to front
        self._add_to_front(node)

    def _add_to_front(self, node: _Node[K, V]) -> None:
        node.prev = self._head
        node.next = self._head.next
        if self._head.next:
            self._head.next.prev = node
        self._head.next = node

    def _remove_node(self, node: _Node[K, V]) -> None:
        if node.prev:
            node.prev.next = node.next
        if node.next:
            node.next.prev = node.prev

    def _remove_lru(self) -> None:
        lru_node = self._tail.prev
        if lru_node and lru_node != self._head:
            del self._cache[lru_node.key]
            self._remove_node(lru_node)

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def capacity(self) -> int:
        return self._capacity

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._head.next = self._tail
            self._tail.prev = self._head

    def contains(self, key: K) -> bool:
        with self._lock:
            return key in self._cache