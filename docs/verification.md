# Offline verification

- UTC: 2026-11-17T06:29:42.334467+00:00
- Python: 3.11.18
- Executed tests: 71
- Passed: 71
- Skipped: 0
- Failures: 0
- Errors: 0
- Elapsed seconds: 0.405

These tests use fake model responses and mocked Docker processes. They do not measure model task quality.
Windows symlink checks may be skipped if link creation is unavailable.

```text
test_empty_evaluation_returns_safe_values (tests.test_evaluation.GlobalMetricsTests.test_empty_evaluation_returns_safe_values) ... ok
test_global_metrics_aggregate_multiple_runs (tests.test_evaluation.GlobalMetricsTests.test_global_metrics_aggregate_multiple_runs) ... ok
test_abort_records_failure_and_clears_pending_requests (tests.test_platform.PlatformTests.test_abort_records_failure_and_clears_pending_requests) ... ok
test_append_updates_only_the_own_file (tests.test_platform.PlatformTests.test_append_updates_only_the_own_file) ... ok
test_approved_tool_failure_is_logged (tests.test_platform.PlatformTests.test_approved_tool_failure_is_logged) ... ok
test_artifacts_are_immutable_and_hash_checked (tests.test_platform.PlatformTests.test_artifacts_are_immutable_and_hash_checked) ... ok
test_clarification_persists_and_resumes (tests.test_platform.PlatformTests.test_clarification_persists_and_resumes) ... ok
test_delete_needs_approval (tests.test_platform.PlatformTests.test_delete_needs_approval) ... ok
test_dependency_waits_and_wakes_after_verified_completion (tests.test_platform.PlatformTests.test_dependency_waits_and_wakes_after_verified_completion) ... ok
test_end_to_end_delegation_review_acceptance_and_second_task (tests.test_platform.PlatformTests.test_end_to_end_delegation_review_acceptance_and_second_task) ... ok
test_exact_replacement_avoids_rewriting_a_large_file (tests.test_platform.PlatformTests.test_exact_replacement_avoids_rewriting_a_large_file) ... ok
test_export_assembles_final_files_in_original_paths (tests.test_platform.PlatformTests.test_export_assembles_final_files_in_original_paths) ... ok
test_export_contains_trace_metrics_artifacts (tests.test_platform.PlatformTests.test_export_contains_trace_metrics_artifacts) ... ok
test_finish_cannot_skip_children (tests.test_platform.PlatformTests.test_finish_cannot_skip_children) ... ok
test_group_survives_reload (tests.test_platform.PlatformTests.test_group_survives_reload) ... ok
test_invalid_finish_cannot_poison_memory (tests.test_platform.PlatformTests.test_invalid_finish_cannot_poison_memory) ... ok
test_invalid_review_does_not_close_review_task (tests.test_platform.PlatformTests.test_invalid_review_does_not_close_review_task) ... ok
test_limits_prevent_unbounded_delegation (tests.test_platform.PlatformTests.test_limits_prevent_unbounded_delegation) ... ok
test_missing_criteria_cannot_pass_review (tests.test_platform.PlatformTests.test_missing_criteria_cannot_pass_review) ... ok
test_model_failure_preserves_queue_and_no_success (tests.test_platform.PlatformTests.test_model_failure_preserves_queue_and_no_success) ... ok
test_next_turn_retains_the_code_action_that_produced_a_file (tests.test_platform.PlatformTests.test_next_turn_retains_the_code_action_that_produced_a_file) ... ok
test_parent_child_dependency_cycle_is_rejected (tests.test_platform.PlatformTests.test_parent_child_dependency_cycle_is_rejected) ... ok
test_plan_cannot_repeat_without_doing_work (tests.test_platform.PlatformTests.test_plan_cannot_repeat_without_doing_work) ... ok
test_private_messages_not_in_other_agent_context (tests.test_platform.PlatformTests.test_private_messages_not_in_other_agent_context) ... ok
test_rejected_review_reopens_task_and_records_lesson (tests.test_platform.PlatformTests.test_rejected_review_reopens_task_and_records_lesson) ... ok
test_repeated_bad_actions_pause_instead_of_loop (tests.test_platform.PlatformTests.test_repeated_bad_actions_pause_instead_of_loop) ... ok
test_replacement_match_mismatch_does_not_modify_file (tests.test_platform.PlatformTests.test_replacement_match_mismatch_does_not_modify_file) ... ok
test_restart_recovers_a_running_task_missing_from_queue (tests.test_platform.PlatformTests.test_restart_recovers_a_running_task_missing_from_queue) ... ok
test_review_context_excludes_plan_and_exposes_current_evidence (tests.test_platform.PlatformTests.test_review_context_excludes_plan_and_exposes_current_evidence) ... ok
test_reviews_require_real_independent_evidence (tests.test_platform.PlatformTests.test_reviews_require_real_independent_evidence) ... ok
test_secret_redaction_in_tool_results (tests.test_platform.PlatformTests.test_secret_redaction_in_tool_results) ... ok
test_self_review_and_self_delegation_forbidden (tests.test_platform.PlatformTests.test_self_review_and_self_delegation_forbidden) ... ok
test_separate_agent_filesystems (tests.test_platform.PlatformTests.test_separate_agent_filesystems) ... ok
test_unexpected_crash_cannot_count_as_success (tests.test_platform.PlatformTests.test_unexpected_crash_cannot_count_as_success) ... ok
test_user_can_reject_final_delivery (tests.test_platform.PlatformTests.test_user_can_reject_final_delivery) ... ok
test_zero_discovered_tests_create_validation_warning (tests.test_platform.PlatformTests.test_zero_discovered_tests_create_validation_warning) ... ok
test_group_ids_cannot_escape_runtime (tests.test_platform.SecurityAndDataTests.test_group_ids_cannot_escape_runtime) ... ok
test_html_scripts_not_in_research_text (tests.test_platform.SecurityAndDataTests.test_html_scripts_not_in_research_text) ... ok
test_path_validation (tests.test_platform.SecurityAndDataTests.test_path_validation) ... ok
test_primary_scenario_prompts_load_without_macos_noise (tests.test_platform.SecurityAndDataTests.test_primary_scenario_prompts_load_without_macos_noise) ... ok
test_private_network_urls_are_blocked (tests.test_platform.SecurityAndDataTests.test_private_network_urls_are_blocked) ... ok
test_symlink_validation (tests.test_platform.SecurityAndDataTests.test_symlink_validation) ... ok
test_hash_mismatch_stops_preview (tests.test_preview.PreviewTests.test_hash_mismatch_stops_preview) ... ok
test_invalid_port_stops_before_launch (tests.test_preview.PreviewTests.test_invalid_port_stops_before_launch) ... ok
test_preview_does_not_approve_or_modify_task_state (tests.test_preview.PreviewTests.test_preview_does_not_approve_or_modify_task_state) ... ok
test_preview_only_mounts_reviewed_source_and_separate_data (tests.test_preview.PreviewTests.test_preview_only_mounts_reviewed_source_and_separate_data) ... ok
test_execution_is_always_in_restricted_docker (tests.test_transport.SandboxBoundaryTests.test_execution_is_always_in_restricted_docker) ... ok
test_missing_docker_does_not_fall_back_to_host (tests.test_transport.SandboxBoundaryTests.test_missing_docker_does_not_fall_back_to_host) ... ok
test_overlapping_human_wait_is_not_double_counted (tests.test_transport.SandboxBoundaryTests.test_overlapping_human_wait_is_not_double_counted) ... ok
test_keyed_search_only_uses_search_credential (tests.test_transport.SearchAndFetchTests.test_keyed_search_only_uses_search_credential) ... ok
test_keyless_search_is_real_api_request_without_model_key (tests.test_transport.SearchAndFetchTests.test_keyless_search_is_real_api_request_without_model_key) ... ok
test_socket_revalidates_dns_and_rejects_rebinding (tests.test_transport.SearchAndFetchTests.test_socket_revalidates_dns_and_rejects_rebinding) ... ok
test_api_key_header_can_be_configured (tests.test_transport.TransportTests.test_api_key_header_can_be_configured) ... ok
test_blocked_network_not_retried (tests.test_transport.TransportTests.test_blocked_network_not_retried) ... ok
test_correct_endpoint_and_json_protocol (tests.test_transport.TransportTests.test_correct_endpoint_and_json_protocol) ... ok
test_http_auth_failure_does_not_echo_response_or_key (tests.test_transport.TransportTests.test_http_auth_failure_does_not_echo_response_or_key) ... ok
test_invalid_json_is_a_recoverable_model_error (tests.test_transport.TransportTests.test_invalid_json_is_a_recoverable_model_error) ... ok
test_missing_key_makes_no_request (tests.test_transport.TransportTests.test_missing_key_makes_no_request) ... ok
test_prohibited_provider_rejected (tests.test_transport.TransportTests.test_prohibited_provider_rejected) ... ok
test_total_deadline_stops_even_if_transport_does_not_timeout (tests.test_transport.TransportTests.test_total_deadline_stops_even_if_transport_does_not_timeout) ... ok
test_transient_http_error_retries (tests.test_transport.TransportTests.test_transient_http_error_retries) ... ok
test_truncated_json_is_not_a_completed_action (tests.test_transport.TransportTests.test_truncated_json_is_not_a_completed_action) ... ok
test_truncated_response_recovers_once_with_short_action_instruction (tests.test_transport.TransportTests.test_truncated_response_recovers_once_with_short_action_instruction) ... ok
test_legacy_directory_lock_is_not_silently_ignored (tests.test_transport.WriterLockTests.test_legacy_directory_lock_is_not_silently_ignored) ... ok
test_lock_releases_after_an_exception (tests.test_transport.WriterLockTests.test_lock_releases_after_an_exception) ... ok
test_two_simultaneous_writers_are_rejected (tests.test_transport.WriterLockTests.test_two_simultaneous_writers_are_rejected) ... ok
test_python_is_parsed_without_importing_or_running_it (tests.test_ui_check.ExtractionTests.test_python_is_parsed_without_importing_or_running_it) ... ok
test_inline_click_handler_is_also_compiled (tests.test_ui_check.JavaScriptRegressionTests.test_inline_click_handler_is_also_compiled) ... ok
test_invalid_javascript_cannot_be_published_as_valid (tests.test_ui_check.JavaScriptRegressionTests.test_invalid_javascript_cannot_be_published_as_valid) ... ok
test_json_script_data_is_not_javascript (tests.test_ui_check.JavaScriptRegressionTests.test_json_script_data_is_not_javascript) ... ok
test_python_string_escape_can_break_every_page_button (tests.test_ui_check.JavaScriptRegressionTests.test_python_string_escape_can_break_every_page_button) ... ok

----------------------------------------------------------------------
Ran 71 tests in 0.405s

OK

```