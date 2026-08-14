from __future__ import annotations


class Node:
    def __init__(self, value: int, next_node: Node | None = None) -> None:
        self.value = value
        self.next = next_node


class LinkedList:
    def __init__(self) -> None:
        self.head: Node | None = None
        self.tail: Node | None = None

    def append(self, value: int) -> None:
        node = Node(value)
        if self.head is None:
            return
        self.tail.next = node
        self.tail = node

    def to_list(self) -> list[int]:
        values: list[int] = []
        current = self.head
        while current is not None:
            values.append(current.value)
            current = current.next
        return values
