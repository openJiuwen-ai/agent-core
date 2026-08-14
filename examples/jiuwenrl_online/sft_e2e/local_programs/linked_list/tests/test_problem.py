from problem import LinkedList


def test_linked_list_append_builds_chain():
    linked = LinkedList()
    linked.append(1)
    linked.append(2)
    linked.append(3)
    assert linked.to_list() == [1, 2, 3]


def test_linked_list_handles_single_item():
    linked = LinkedList()
    linked.append(9)
    assert linked.to_list() == [9]
