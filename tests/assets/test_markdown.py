from assets.markdown import extract_asset_ids


def test_extracts_only_positive_image_scheme_ids_in_source_order() -> None:
    markdown = """
![one](image://12)
[not image](image://13)
![zero](image://0)
![remote](https://example.com/a.png)
![duplicate](image://12)
![title]( image://7 "caption" )
"""
    assert extract_asset_ids(markdown) == (12, 7)


def test_ignores_image_tokens_inside_inline_and_fenced_code() -> None:
    markdown = """
`![inline](image://1)`
```md
![fenced](image://2)
```
~~~
![also-fenced](image://3)
~~~
![real](image://4)
"""
    assert extract_asset_ids(markdown) == (4,)
