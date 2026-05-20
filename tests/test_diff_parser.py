from pr_sentinel import diff_parser

MODIFY_DIFF = """diff --git a/src/UserService.cs b/src/UserService.cs
index 1111111..2222222 100644
--- a/src/UserService.cs
+++ b/src/UserService.cs
@@ -1,3 +1,5 @@
 public class UserService {
+    private const string Token = "abc";
+    // new behavior
 }
"""

ADD_DIFF = """diff --git a/src/NewFile.cs b/src/NewFile.cs
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/NewFile.cs
@@ -0,0 +1,2 @@
+namespace X;
+public class NewFile {}
"""

DELETE_DIFF = """diff --git a/old.txt b/old.txt
deleted file mode 100644
index 1111111..0000000
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-line one
-line two
"""

NOISE_DIFF = """diff --git a/package-lock.json b/package-lock.json
index 1111111..2222222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,1 +1,1 @@
-old
+new
diff --git a/dist/bundle.min.js b/dist/bundle.min.js
index 1111111..2222222 100644
--- a/dist/bundle.min.js
+++ b/dist/bundle.min.js
@@ -1,1 +1,1 @@
-old
+new
diff --git a/src/keep.py b/src/keep.py
index 1111111..2222222 100644
--- a/src/keep.py
+++ b/src/keep.py
@@ -1,1 +1,1 @@
-old
+new
"""


def test_empty_input_returns_empty_list():
    assert diff_parser.parse("") == []
    assert diff_parser.parse("   \n  \n") == []


def test_non_diff_input_returns_empty_list():
    assert diff_parser.parse("random text with no diff headers") == []


def test_modified_file_parsed_with_counts():
    files = diff_parser.parse(MODIFY_DIFF)
    assert len(files) == 1
    f = files[0]
    assert f["filePath"] == "src/UserService.cs"
    assert f["changeType"] == "modified"
    assert f["addedLines"] == 2
    assert f["removedLines"] == 0
    assert "private const string Token" in f["diff"]


def test_added_file_classified_correctly():
    files = diff_parser.parse(ADD_DIFF)
    assert files[0]["changeType"] == "added"
    assert files[0]["filePath"] == "src/NewFile.cs"


def test_deleted_file_classified_correctly():
    files = diff_parser.parse(DELETE_DIFF)
    assert files[0]["changeType"] == "deleted"
    assert files[0]["removedLines"] == 2


def test_noise_files_filtered():
    files = diff_parser.parse(NOISE_DIFF)
    paths = [f["filePath"] for f in files]
    assert paths == ["src/keep.py"]


def test_oversized_diff_truncated():
    huge_body = "+" + ("x" * 50_000) + "\n"
    big = (
        "diff --git a/big.txt b/big.txt\n"
        "index 1111111..2222222 100644\n"
        "--- a/big.txt\n"
        "+++ b/big.txt\n"
        "@@ -0,0 +1,1 @@\n"
        + huge_body
    )
    files = diff_parser.parse(big, max_file_size=1000)
    assert len(files[0]["diff"]) <= 1000 + 200  # cap + truncation note
    assert "truncated by pr-sentinel" in files[0]["diff"]


def test_multiple_files_preserve_order():
    combined = MODIFY_DIFF + ADD_DIFF
    files = diff_parser.parse(combined)
    assert [f["filePath"] for f in files] == ["src/UserService.cs", "src/NewFile.cs"]
