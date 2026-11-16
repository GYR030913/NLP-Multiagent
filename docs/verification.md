# Offline verification

- UTC: 2026-11-16T20:06:49.368912+00:00
- Python: 3.10.11
- Executed tests: 79
- Passed: 78
- Skipped: 1
- Failures: 0
- Errors: 0
- Elapsed seconds: 4.297

These tests use fake model responses and mocked Docker processes. They do not measure model task quality.
Windows symlink checks may be skipped if link creation is unavailable.

```text
test_agent_direct_message_is_recipient_only (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_cross_agent_dependency_cycle_is_rejected_atomically (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_depth_limit_does_not_reset_when_agent_changes (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_fake_model_drives_plan_then_delegation (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_multiple_dependencies_wake_only_after_every_prerequisite (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_rejection_after_three_reworks_fails_without_success_metrics (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_restart_recovers_running_tasks_and_skips_waiting_states (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_second_task_imports_artifact_from_first_task (tests.test_member_a_orchestration.MemberAOrchestrationTests) ... ok
test_abort_records_failure_and_clears_pending_requests (tests.test_platform.PlatformTests) ... ok
test_append_updates_only_the_own_file (tests.test_platform.PlatformTests) ... ok
test_approved_tool_failure_is_logged (tests.test_platform.PlatformTests) ... ok
test_artifacts_are_immutable_and_hash_checked (tests.test_platform.PlatformTests) ... ok
test_clarification_persists_and_resumes (tests.test_platform.PlatformTests) ... ok
test_delete_needs_approval (tests.test_platform.PlatformTests) ... ok
test_dependency_waits_and_wakes_after_verified_completion (tests.test_platform.PlatformTests) ... ok
test_end_to_end_delegation_review_acceptance_and_second_task (tests.test_platform.PlatformTests) ... ok
test_exact_replacement_avoids_rewriting_a_large_file (tests.test_platform.PlatformTests) ... ok
test_export_assembles_final_files_in_original_paths (tests.test_platform.PlatformTests) ... ok
test_export_contains_trace_metrics_artifacts (tests.test_platform.PlatformTests) ... ok
test_finish_cannot_skip_children (tests.test_platform.PlatformTests) ... ok
test_group_survives_reload (tests.test_platform.PlatformTests) ... ok
test_invalid_finish_cannot_poison_memory (tests.test_platform.PlatformTests) ... ok
test_invalid_review_does_not_close_review_task (tests.test_platform.PlatformTests) ... ok
test_limits_prevent_unbounded_delegation (tests.test_platform.PlatformTests) ... ok
test_missing_criteria_cannot_pass_review (tests.test_platform.PlatformTests) ... ok
test_model_failure_preserves_queue_and_no_success (tests.test_platform.PlatformTests) ... ok
test_next_turn_retains_the_code_action_that_produced_a_file (tests.test_platform.PlatformTests) ... ok
test_parent_child_dependency_cycle_is_rejected (tests.test_platform.PlatformTests) ... ok
test_plan_cannot_repeat_without_doing_work (tests.test_platform.PlatformTests) ... ok
test_private_messages_not_in_other_agent_context (tests.test_platform.PlatformTests) ... ok
test_rejected_review_reopens_task_and_records_lesson (tests.test_platform.PlatformTests) ... ok
test_repeated_bad_actions_pause_instead_of_loop (tests.test_platform.PlatformTests) ... ok
test_replacement_match_mismatch_does_not_modify_file (tests.test_platform.PlatformTests) ... ok
test_restart_recovers_a_running_task_missing_from_queue (tests.test_platform.PlatformTests) ... ok
test_review_context_excludes_plan_and_exposes_current_evidence (tests.test_platform.PlatformTests) ... ok
test_reviews_require_real_independent_evidence (tests.test_platform.PlatformTests) ... ok
test_secret_redaction_in_tool_results (tests.test_platform.PlatformTests) ... ok
test_self_review_and_self_delegation_forbidden (tests.test_platform.PlatformTests) ... ok
test_separate_agent_filesystems (tests.test_platform.PlatformTests) ... ok
test_unexpected_crash_cannot_count_as_success (tests.test_platform.PlatformTests) ... ok
test_user_can_reject_final_delivery (tests.test_platform.PlatformTests) ... ok
test_zero_discovered_tests_create_validation_warning (tests.test_platform.PlatformTests) ... ok
test_group_ids_cannot_escape_runtime (tests.test_platform.SecurityAndDataTests) ... ok
test_html_scripts_not_in_research_text (tests.test_platform.SecurityAndDataTests) ... ok
test_path_validation (tests.test_platform.SecurityAndDataTests) ... ok
test_primary_scenario_prompts_load_without_macos_noise (tests.test_platform.SecurityAndDataTests) ... ok
test_private_network_urls_are_blocked (tests.test_platform.SecurityAndDataTests) ... ok
test_symlink_validation (tests.test_platform.SecurityAndDataTests) ... skipped 'Symlink creation not permitted on this Windows host'
test_hash_mismatch_stops_preview (tests.test_preview.PreviewTests) ... ok
test_invalid_port_stops_before_launch (tests.test_preview.PreviewTests) ... ok
test_preview_does_not_approve_or_modify_task_state (tests.test_preview.PreviewTests) ... ok
test_preview_only_mounts_reviewed_source_and_separate_data (tests.test_preview.PreviewTests) ... ok
test_execution_is_always_in_restricted_docker (tests.test_transport.SandboxBoundaryTests) ... ok
test_missing_docker_does_not_fall_back_to_host (tests.test_transport.SandboxBoundaryTests) ... ok
test_overlapping_human_wait_is_not_double_counted (tests.test_transport.SandboxBoundaryTests) ... ok
test_keyed_search_only_uses_search_credential (tests.test_transport.SearchAndFetchTests) ... ok
test_keyless_search_is_real_api_request_without_model_key (tests.test_transport.SearchAndFetchTests) ... ok
test_socket_revalidates_dns_and_rejects_rebinding (tests.test_transport.SearchAndFetchTests) ... ok
test_api_key_header_can_be_configured (tests.test_transport.TransportTests) ... ok
test_blocked_network_not_retried (tests.test_transport.TransportTests) ... ok
test_configured_total_deadline_is_honored (tests.test_transport.TransportTests) ... ok
test_correct_endpoint_and_json_protocol (tests.test_transport.TransportTests) ... ok
test_http_auth_failure_does_not_echo_response_or_key (tests.test_transport.TransportTests) ... ok
test_invalid_json_is_a_recoverable_model_error (tests.test_transport.TransportTests) ... ok
test_missing_key_makes_no_request (tests.test_transport.TransportTests) ... ok
test_prohibited_provider_rejected (tests.test_transport.TransportTests) ... ok
test_remote_disconnect_is_retried_as_a_transport_error (tests.test_transport.TransportTests) ... ok
test_total_deadline_stops_even_if_transport_does_not_timeout (tests.test_transport.TransportTests) ... ok
test_transient_http_error_retries (tests.test_transport.TransportTests) ... ok
test_truncated_json_is_not_a_completed_action (tests.test_transport.TransportTests) ... ok
test_truncated_response_recovers_once_with_short_action_instruction (tests.test_transport.TransportTests) ... ok
test_legacy_directory_lock_is_not_silently_ignored (tests.test_transport.WriterLockTests) ... ok
test_lock_releases_after_an_exception (tests.test_transport.WriterLockTests) ... ok
test_two_simultaneous_writers_are_rejected (tests.test_transport.WriterLockTests) ... ok
test_python_is_parsed_without_importing_or_running_it (tests.test_ui_check.ExtractionTests) ... ok
test_inline_click_handler_is_also_compiled (tests.test_ui_check.JavaScriptRegressionTests) ... ok
test_invalid_javascript_cannot_be_published_as_valid (tests.test_ui_check.JavaScriptRegressionTests) ... ok
test_json_script_data_is_not_javascript (tests.test_ui_check.JavaScriptRegressionTests) ... ok
test_python_string_escape_can_break_every_page_button (tests.test_ui_check.JavaScriptRegressionTests) ... ok

----------------------------------------------------------------------
Ran 79 tests in 4.295s

OK (skipped=1)

```