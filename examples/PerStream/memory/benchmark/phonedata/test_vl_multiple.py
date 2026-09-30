"""
Test if VL API supports multiple images in one call
"""
import sys
import os
import base64
import requests
import json
import time

API_KEY = "Bearer "
API_URL = ""
MODEL_VL = "qwen35-35b-vl"

print("=" * 60)
print("Test: Can VL API accept multiple images in one call?")
print("=" * 60)

test_images = [
    "zipdata/aitw_images/general/10039701842972332757_0.png",
    "zipdata/aitw_images/general/10039701842972332757_1.png",
    "zipdata/aitw_images/general/10039701842972332757_10.png"
]

print(f"\nTest images: {len(test_images)}")
for i, img in enumerate(test_images, 1):
    print(f"  {i}. {os.path.basename(img)}")

print("\nEncoding images...")
encoded_images = []
for i, img in enumerate(test_images, 1):
    with open(img, 'rb') as f:
        enc = base64.b64encode(f.read()).decode('utf-8')
    encoded_images.append(enc)
    print(f"  Image {i}: {len(enc)} chars")

print("\nBuilding request payload with multiple images...")
content_list = []
for enc in encoded_images:
    content_list.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{enc}"}
    })

content_list.append({
    "type": "text",
    "text": "Describe all these screenshots in order. What do you see in each one?"
})

headers = {
    "Authorization": API_KEY,
    "Content-Type": "application/json"
}

payload = {
    "model": MODEL_VL,
    "messages": [
        {
            "role": "system",
            "content": "You are an expert at analyzing mobile phone screenshots."
        },
        {
            "role": "user",
            "content": content_list
        }
    ],
    "max_tokens": 800
}

print(f"Content list length: {len(content_list)}")
print(f"Images in content: {len([c for c in content_list if c['type'] == 'image_url'])}")

print("\nSending request...")
start = time.time()

try:
    response = requests.post(API_URL, headers=headers, json=payload, timeout=90)
    
    elapsed = time.time() - start
    print(f"Request completed in {elapsed:.1f}s")
    print(f"Status code: {response.status_code}")
    
    if response.status_code == 200:
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        print(f"\n✓ SUCCESS!")
        print(f"Response length: {len(content)} chars")
        print(f"\nResponse preview:\n{content[:500]}...")
        
        with open("test_multiple_vl_response.json", "w", encoding="utf-8") as f:
            json.dump({"response": content, "duration": elapsed}, f, ensure_ascii=False, indent=2)
        
        print(f"\nSaved: test_multiple_vl_response.json")
        
    else:
        print(f"\n✗ FAILED!")
        print(f"Response: {response.text[:500]}")
        
except Exception as e:
    elapsed = time.time() - start
    print(f"\n✗ ERROR after {elapsed:.1f}s: {e}")

print(f"\n{'=' * 60}")