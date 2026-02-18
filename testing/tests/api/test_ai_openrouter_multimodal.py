from pymol.ai.openrouter_client import build_multimodal_user_content


def test_multimodal_payload_with_image():
    content = build_multimodal_user_content("validate", "data:image/png;base64,AAA")
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_multimodal_payload_without_image():
    content = build_multimodal_user_content("validate", None)
    assert len(content) == 1
    assert content[0]["type"] == "text"
