from app.products.openai.video import _extract_video_url_candidates


def test_video_url_fallback_parser_accepts_final_url_keys():
    payload = {
        "result": {
            "response": {
                "streamingVideoGenerationResponse": {
                    "progress": 100,
                    "final_url": "https://example.com/file/generated_video.mp4/n",
                }
            }
        }
    }

    assert _extract_video_url_candidates(payload) == [
        "https://example.com/file/generated_video.mp4"
    ]


def test_video_url_fallback_parser_accepts_playback_url_keys():
    payload = {
        "video": {
            "playbackUrl": "https:\\/\\/example.com\\/share-videos\\/abc.mp4\\n"
        }
    }

    assert _extract_video_url_candidates(payload) == [
        "https://example.com/share-videos/abc.mp4"
    ]
