import hashlib
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

# Attempt to use the actual User and Project models from the application
# These paths are common in Label Studio, but might need adjustment if tests fail
try:
    from users.models import User
except ImportError:
    from django.contrib.auth.models import User # Fallback for simpler environments

try:
    from projects.models import Project
except ImportError: # Fallback if projects.models.Project is not found directly (e.g. enterprise)
    # This is a placeholder. A more robust solution might be needed if Project model is complex
    # or has specific creation requirements not met by a simple model.
    # For now, we hope the primary import works.
    # If projects.models.Project is not the correct path, test execution will guide.
    Project = None # Will cause errors if not correctly imported and used.

from data_import.models import FileUpload
from data_import.uploader import create_file_upload

# Basic logging for test debugging, if necessary
import logging
logger = logging.getLogger(__name__)

class FileHashAndDuplicateTest(TestCase):

    def setUp(self):
        # Ensure Project model is available
        if Project is None:
            self.fail("Project model could not be imported. Update test file with correct import path.")

        self.user, _ = User.objects.get_or_create(username='testuser_hash_test')
        if hasattr(self.user, 'set_password') and not self.user.has_usable_password():
             self.user.set_password('password')
             self.user.save()

        # For projects, ensure created_by is handled. If it's a OneToOne or ForeignKey to User:
        self.project1 = Project.objects.create(title='Test Project Hash 1', created_by=self.user)
        self.project2 = Project.objects.create(title='Test Project Hash 2', created_by=self.user)

        # Add a simple label config to ensure one_object_in_label_config is True
        # This helps .txt files to be processed as a single task.
        simple_label_config = '<View><Text name="text" value="$text_file"/></View>'
        self.project1.label_config = simple_label_config
        self.project1.save()
        self.project2.label_config = simple_label_config
        self.project2.save()

        self.file_content1 = b"This is a test file content for hashing."
        self.file_hash1 = hashlib.sha256(self.file_content1).hexdigest()
        self.file_content2 = b"This is a different test file content for hashing."
        self.file_hash2 = hashlib.sha256(self.file_content2).hexdigest()

    def _upload_file(self, project, filename, content):
        f = SimpleUploadedFile(filename, content)
        # Directly call create_file_upload to ensure direct test of its logic
        file_upload_instance = create_file_upload(self.user, project, f)
        return file_upload_instance

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_hash_calculated_on_upload_when_flag_enabled(self):
        file_upload = self._upload_file(self.project1, "test1_hash_enabled.txt", self.file_content1)
        self.assertEqual(file_upload.file_hash, self.file_hash1)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=False)
    def test_hash_not_calculated_on_upload_when_flag_disabled(self):
        file_upload = self._upload_file(self.project1, "test1_hash_disabled.txt", self.file_content1)
        self.assertIsNone(file_upload.file_hash)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_duplicate_file_skipped_in_same_project(self):
        fu1 = self._upload_file(self.project1, "file1_orig_same_proj.txt", self.file_content1)
        self.assertEqual(fu1.file_hash, self.file_hash1)

        fu2 = self._upload_file(self.project1, "file1_dup_same_proj.txt", self.file_content1)
        self.assertEqual(fu2.file_hash, self.file_hash1)

        fu3 = self._upload_file(self.project1, "file2_diff_same_proj.txt", self.file_content2)
        self.assertEqual(fu3.file_hash, self.file_hash2)

        file_upload_ids = [fu1.id, fu2.id, fu3.id]
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, file_upload_ids)

        self.assertEqual(len(tasks), 2)
        task_file_upload_ids = {task['file_upload_id'] for task in tasks}
        self.assertIn(fu1.id, task_file_upload_ids)
        self.assertNotIn(fu2.id, task_file_upload_ids)
        self.assertIn(fu3.id, task_file_upload_ids)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_duplicate_file_not_skipped_in_different_projects(self):
        fu1_p1 = self._upload_file(self.project1, "file_p1_diff_proj.txt", self.file_content1)
        # Same content, different project
        fu1_p2 = self._upload_file(self.project2, "file_p2_diff_proj.txt", self.file_content1)

        self.assertEqual(fu1_p1.file_hash, self.file_hash1)
        self.assertEqual(fu1_p2.file_hash, self.file_hash1)

        tasks_p1, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, [fu1_p1.id])
        self.assertEqual(len(tasks_p1), 1)

        tasks_p2, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project2, [fu1_p2.id])
        self.assertEqual(len(tasks_p2), 1)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=False)
    def test_duplicate_file_not_skipped_when_flag_disabled(self):
        fu1 = self._upload_file(self.project1, "file1_orig_flag_off.txt", self.file_content1)
        # Same content, flag off
        fu2 = self._upload_file(self.project1, "file1_dup_flag_off.txt", self.file_content1)

        self.assertIsNone(fu1.file_hash)
        self.assertIsNone(fu2.file_hash)

        file_upload_ids = [fu1.id, fu2.id]
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, file_upload_ids)
        self.assertEqual(len(tasks), 2)
        task_file_upload_ids = {task['file_upload_id'] for task in tasks}
        self.assertIn(fu1.id, task_file_upload_ids)
        self.assertIn(fu2.id, task_file_upload_ids)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_no_hash_no_skip(self):
        # Simulate a FileUpload entry that somehow doesn't have a hash (e.g., old record)
        # even when the flag is on.

        # Create a SimpleUploadedFile for the 'file' field
        suf_no_hash = SimpleUploadedFile("no_hash_file.txt", self.file_content1)
        fu_no_hash = FileUpload.objects.create(
            user=self.user,
            project=self.project1,
            file=suf_no_hash, # Assign the SimpleUploadedFile instance
            file_hash=None # Explicitly None
        )
        # Ensure the file is closed if opened by FileUpload creation or other processes
        if hasattr(suf_no_hash, 'close') and not suf_no_hash.closed:
            suf_no_hash.close()

        fu_with_hash = self._upload_file(self.project1, "hashed_file_for_no_hash_test.txt", self.file_content2)

        file_upload_ids = [fu_no_hash.id, fu_with_hash.id]
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, file_upload_ids)

        self.assertEqual(len(tasks), 2)
        task_file_upload_ids = {task['file_upload_id'] for task in tasks}
        self.assertIn(fu_no_hash.id, task_file_upload_ids)
        self.assertIn(fu_with_hash.id, task_file_upload_ids)

    # Consider adding a test for files that are different but have same hash (collision - though unlikely for SHA256)
    # For now, focusing on the direct requirements.

    # Test with empty file content
    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_empty_file_hash_calculated(self):
        empty_content = b""
        empty_hash = hashlib.sha256(empty_content).hexdigest()
        file_upload = self._upload_file(self.project1, "empty.txt", empty_content)
        self.assertEqual(file_upload.file_hash, empty_hash)

        # Upload another empty file, should be skipped
        file_upload2 = self._upload_file(self.project1, "empty_dup.txt", empty_content)
        self.assertEqual(file_upload2.file_hash, empty_hash)

        file_upload_ids = [file_upload.id, file_upload2.id]
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, file_upload_ids)
        # Empty .txt files produce 0 tasks via read_tasks_list_from_txt.
        # The first empty file is processed (0 tasks), the second is skipped as a duplicate.
        self.assertEqual(len(tasks), 0)

    # Test SVG cleaning interaction (ensure hashing happens, and SVG cleaning still works)
    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True, SVG_SECURITY_CLEANUP=True)
    def test_hashing_with_svg_cleaning(self):
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert("XSS")</script><rect width="10" height="10"/></svg>'
        # Hash is of the original content
        expected_hash = hashlib.sha256(svg_content).hexdigest()

        file_upload = self._upload_file(self.project1, "test.svg", svg_content)
        self.assertEqual(file_upload.file_hash, expected_hash)

        # Verify that the file content was cleaned (script tag removed)
        file_upload.file.seek(0)
        cleaned_content = file_upload.file.read()
        self.assertNotIn(b"<script>", cleaned_content)
        self.assertIn(b"<rect", cleaned_content)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_file_with_no_extension_is_hashed(self):
        file_content_no_ext = b"Content for a file with no extension."
        expected_hash_no_ext = hashlib.sha256(file_content_no_ext).hexdigest()

        file_upload = self._upload_file(self.project1, "file_no_ext", file_content_no_ext)
        self.assertEqual(file_upload.file_hash, expected_hash_no_ext)

        # Ensure tasks are created (assuming default behavior for unknown file types is one task)
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, [file_upload.id])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['file_upload_id'], file_upload.id)

    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_multiple_different_files_no_skipping(self):
        fu1 = self._upload_file(self.project1, "multi_diff1.txt", self.file_content1)
        fu2 = self._upload_file(self.project1, "multi_diff2.txt", self.file_content2)
        content3 = b"Yet another different content."
        hash3 = hashlib.sha256(content3).hexdigest()
        fu3 = self._upload_file(self.project1, "multi_diff3.txt", content3)

        self.assertEqual(fu1.file_hash, self.file_hash1)
        self.assertEqual(fu2.file_hash, self.file_hash2)
        self.assertEqual(fu3.file_hash, hash3)

        file_upload_ids = [fu1.id, fu2.id, fu3.id]
        tasks, _, _ = FileUpload.load_tasks_from_uploaded_files(self.project1, file_upload_ids)

        self.assertEqual(len(tasks), 3)
        task_file_upload_ids = {task['file_upload_id'] for task in tasks}
        self.assertIn(fu1.id, task_file_upload_ids)
        self.assertIn(fu2.id, task_file_upload_ids)
        self.assertIn(fu3.id, task_file_upload_ids)

    # Test case where file.tell() might not be 0 initially in _upload_file,
    # though SimpleUploadedFile usually starts at 0.
    # This is more of a check on the robustness of create_file_upload's pointer handling.
    @override_settings(RECORD_FILE_HASH_AND_PREVENT_DUPLICATES=True)
    def test_file_pointer_reset_robustness(self):
        filename = "pointer_test.txt"
        f = SimpleUploadedFile(filename, self.file_content1)
        # Manually advance pointer before passing to create_file_upload (simulates unusual state)
        f.seek(5)
        current_pos_before_upload = f.tell()

        file_upload_instance = create_file_upload(self.user, self.project1, f)

        self.assertEqual(file_upload_instance.file_hash, self.file_hash1)
        # Check if file pointer is reset to what it was before hashing (which was `current_pos_before_upload`)
        # or to 0 if create_file_upload is expected to always ensure it's fully saved.
        # The current logic in create_file_upload resets to `original_position`.
        # After instance.file = file, and instance.save(), Django's FileField handling
        # will typically read from the current position of the file object.
        # If original_position was > 0, and it was reset to that, then save might only save part of the file.
        # This needs to be correct: `file.seek(original_position)` before hashing,
        # and then `file.seek(original_position)` AGAIN before `instance.file = file` or `instance.save()`.
        # The provided code in step 3 for uploader.py was:
        #   file.seek(original_position) # after reading for hash
        #   instance.file = file # then assigned
        #   instance.save()
        # Django's save should handle reading from the start if the pointer is at original_position.
        # Let's verify the saved file size or content if possible, or that no error occurs.
        # For now, just ensuring hash is correct implies full read.

        # Re-fetch to check saved file properties
        re_fetched_fu = FileUpload.objects.get(id=file_upload_instance.id)
        re_fetched_fu.file.seek(0)
        saved_content = re_fetched_fu.file.read()
        self.assertEqual(saved_content, self.file_content1, "Full file content should be saved.")
        re_fetched_fu.file.close()

        # Cleanup: Close the manually created SimpleUploadedFile
        if hasattr(f, 'close') and not f.closed:
            f.close()

# TODO: Add tests for URL imports if time permits.
# The tasks_from_url function in uploader.py uses create_file_upload.
# Testing create_file_upload thoroughly covers the hashing part.
# Testing load_tasks_from_uploaded_files covers the skipping part.
# A separate test for tasks_from_url could ensure the SimpleUploadedFile creation
# and its interaction with create_file_upload is correct under these flags.
# For now, this set of tests focuses on the core logic.

# Ensure to clean up created files if they are stored on disk and not in memory.
# Django's test runner usually handles media file cleanup for FileField if configured.
