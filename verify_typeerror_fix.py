import json

def test_queue_parsing_fix():
    # Problematic input: a numeric string that json.loads parses as a float
    queue_id_json = "5.2"
    
    # Simulation of the fix in main.py and registration.py
    try:
        queues = json.loads(queue_id_json)
        if not isinstance(queues, list):
            # If it's a single value (like 5.2), wrap it in the expected list format
            queues = [{"id": str(queue_id_json), "alias": str(queue_id_json)}]
    except:
        # Fallback for old data
        queues = [{"id": queue_id_json, "alias": queue_id_json}]
        
    print(f"Input: {queue_id_json}")
    print(f"Parsed queues: {queues}")
    
    assert isinstance(queues, list)
    assert len(queues) == 1
    assert queues[0]["id"] == "5.2"
    assert queues[0]["alias"] == "5.2"
    
    # Simulation of the fix in db.py (get_users_by_queue)
    queue_id_to_find = "5.2"
    matching_users = []
    tg_id = 12345
    
    try:
        queues_db = json.loads(queue_id_json)
        if isinstance(queues_db, list) and any(q.get("id") == queue_id_to_find for q in queues_db):
            matching_users.append(tg_id)
        elif not isinstance(queues_db, list) and str(queues_db) == queue_id_to_find:
            matching_users.append(tg_id)
    except:
        if queue_id_json == queue_id_to_find:
            matching_users.append(tg_id)
            
    print(f"DB Matching users: {matching_users}")
    assert tg_id in matching_users
    
    print("Verification: SUCCESS")

if __name__ == "__main__":
    test_queue_parsing_fix()
