from datetime import date
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Feature, Role, Users
from apps.dashboard.models import (
    CategoryMapping,
    DashboardAsinMonthlySummary,
    FlipkartCategoryMap,
    FlipkartPLA,
    FlipkartPrice,
    FlipkartProcessedDashboardData,
    FlipkartSearchTraffic,
    PriceData,
    ProcessedDashboardData,
    SalesData,
    SpendData,
)
from apps.dashboard.services.analytics_services_orm_pipeline import run_orm_computation
from apps.dashboard.services.daily_summary import rebuild_daily_summary_for_user
from apps.dashboard.tasks import refresh_dashboard_inventory_summary_task
from apps.upload.tasks import _mark_batch_task_complete, _run_dashboard_refresh
from apps.upload.views import _register_upload_batch


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dashboard-tests",
        }
    }
)
class DashboardEndpointIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.feature_business = Feature.objects.create(
            name="Business Dashboard", code_name="business_dashboard"
        )
        cls.feature_upload = Feature.objects.create(
            name="Upload Data", code_name="upload_data"
        )

        cls.main_user = Users.objects.create(
            fname="Main",
            lname="Owner",
            email="main-owner@example.com",
            pswd="secret",
            cpswd="secret",
        )

        dashboard_role = Role.objects.create(
            name="Dashboard Role", created_by=cls.main_user
        )
        dashboard_role.features.add(cls.feature_business)

        upload_only_role = Role.objects.create(
            name="Upload Only Role", created_by=cls.main_user
        )
        upload_only_role.features.add(cls.feature_upload)

        cls.allowed_sub_user = Users.objects.create(
            fname="Allowed",
            lname="User",
            email="allowed-sub@example.com",
            pswd="secret",
            cpswd="secret",
            created_by=cls.main_user,
            role=dashboard_role,
        )
        cls.denied_sub_user = Users.objects.create(
            fname="Denied",
            lname="User",
            email="denied-sub@example.com",
            pswd="secret",
            cpswd="secret",
            created_by=cls.main_user,
            role=upload_only_role,
        )

        rows = []
        base_date = date(2026, 5, 1)
        for i in range(1, 261):
            rows.append(
                ProcessedDashboardData(
                    user=cls.main_user,
                    date=base_date,
                    asin=f"ASIN{i:04d}",
                    portfolio=f"Portfolio {i % 6}",
                    category=f"Category {i % 8}",
                    subcategory=f"Subcategory {i % 10}",
                    price=499.0 + i,
                    pageviews=100 + i,
                    units=max(1, i % 11),
                    orders=max(1, i % 9),
                    revenue=1000.0 + i * 5,
                    spend_sp=80.0 + (i % 5),
                    spend_sb=20.0 + (i % 3),
                    spend_sd=10.0 + (i % 2),
                    total_spend=110.0 + (i % 7),
                )
            )
        ProcessedDashboardData.objects.bulk_create(rows, batch_size=500)

    def _login(self, user):
        session = self.client.session
        session["user_id"] = user.id
        session.save()

    def test_dashboard_section_denies_sub_user_without_dashboard_feature(self):
        self._login(self.denied_sub_user)
        response = self.client.get(
            reverse(
                "dashboard-section",
                kwargs={"view_name": "business", "section": "overview"},
            )
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "Permission denied.")

    def test_dashboard_section_allows_sub_user_with_feature(self):
        self._login(self.allowed_sub_user)
        response = self.client.get(
            reverse(
                "dashboard-section",
                kwargs={"view_name": "business", "section": "overview"},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total Revenue")

    def test_filter_dropdown_denies_sub_user_without_dashboard_feature(self):
        self._login(self.denied_sub_user)
        response = self.client.get(
            reverse("dashboard-filter-options"),
            {"field": "asin", "page": 1, "page_size": 50},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "Permission denied.")

    def test_filter_dropdown_paginates_large_dataset(self):
        self._login(self.main_user)
        response = self.client.get(
            reverse("dashboard-filter-options"),
            {"field": "asin", "page": 1, "page_size": 50},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["field"], "asin")
        self.assertEqual(len(payload["results"]), 50)
        self.assertEqual(payload["pagination"]["total"], 260)
        self.assertTrue(payload["pagination"]["has_next"])

        response_last_page = self.client.get(
            reverse("dashboard-filter-options"),
            {"field": "asin", "page": 6, "page_size": 50},
        )
        self.assertEqual(response_last_page.status_code, 200)
        payload_last = response_last_page.json()
        self.assertEqual(len(payload_last["results"]), 10)
        self.assertFalse(payload_last["pagination"]["has_next"])

    def test_filter_dropdown_search_is_case_insensitive(self):
        self._login(self.main_user)
        response = self.client.get(
            reverse("dashboard-filter-options"),
            {"field": "asin", "q": "asin02", "page": 1, "page_size": 100},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(len(payload["results"]), 0)
        for row in payload["results"]:
            self.assertIn("ASIN02", row["value"])

    def test_refresh_now_invalidates_owner_dashboard_cache_for_sub_user(self):
        self._login(self.allowed_sub_user)
        version_key = f"dashboard_data_version_{self.main_user.id}"
        filter_key = f"dashboard_filters_{self.main_user.id}_True_True"
        cache.set(version_key, 7, timeout=None)
        cache.set(filter_key, {"cached": True}, timeout=300)

        response = self.client.get(reverse("dashboard-refresh-now"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(cache.get(version_key), 8)
        self.assertIsNone(cache.get(filter_key))
        self.assertEqual(
            response["Cache-Control"],
            "no-cache, no-store, must-revalidate, max-age=0",
        )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dashboard-summary-tests",
        }
    }
)
class DashboardSummaryFastPathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Users.objects.create(
            fname="Summary",
            lname="Owner",
            email="summary-owner@example.com",
            pswd="secret",
            cpswd="secret",
        )

        CategoryMapping.objects.bulk_create(
            [
                CategoryMapping(
                    user=cls.user,
                    asin="ASIN-ALPHA",
                    portfolio="Planters",
                    category="Indoor",
                    subcategory="Ceramic",
                ),
                CategoryMapping(
                    user=cls.user,
                    asin="ASIN-BETA",
                    portfolio="Planters",
                    category="Outdoor",
                    subcategory="Metal",
                ),
            ]
        )

        rows = [
            ProcessedDashboardData(
                user=cls.user,
                date=date(2026, 1, 10),
                asin="ASIN-ALPHA",
                portfolio="Planters",
                category="Indoor",
                subcategory="Ceramic",
                price=499.0,
                pageviews=120,
                units=8,
                orders=7,
                revenue=8000.0,
                spend_sp=600.0,
                spend_sb=100.0,
                spend_sd=50.0,
                total_spend=750.0,
            ),
            ProcessedDashboardData(
                user=cls.user,
                date=date(2026, 1, 11),
                asin="ASIN-BETA",
                portfolio="Planters",
                category="Outdoor",
                subcategory="Metal",
                price=699.0,
                pageviews=80,
                units=5,
                orders=5,
                revenue=5000.0,
                spend_sp=300.0,
                spend_sb=60.0,
                spend_sd=40.0,
                total_spend=400.0,
            ),
            ProcessedDashboardData(
                user=cls.user,
                date=date(2026, 2, 10),
                asin="ASIN-ALPHA",
                portfolio="Planters",
                category="Indoor",
                subcategory="Ceramic",
                price=499.0,
                pageviews=180,
                units=12,
                orders=10,
                revenue=12000.0,
                spend_sp=700.0,
                spend_sb=120.0,
                spend_sd=80.0,
                total_spend=900.0,
            ),
            ProcessedDashboardData(
                user=cls.user,
                date=date(2026, 2, 11),
                asin="ASIN-BETA",
                portfolio="Planters",
                category="Outdoor",
                subcategory="Metal",
                price=699.0,
                pageviews=60,
                units=0,
                orders=0,
                revenue=0.0,
                spend_sp=0.0,
                spend_sb=0.0,
                spend_sd=0.0,
                total_spend=0.0,
            ),
        ]
        ProcessedDashboardData.objects.bulk_create(rows)
        FlipkartProcessedDashboardData.objects.bulk_create(
            [
                FlipkartProcessedDashboardData(
                    user=cls.user,
                    date=date(2026, 2, 10),
                    fsn="FSN-ALPHA",
                    portfolio="Planters",
                    category="Indoor",
                    subcategory="Ceramic",
                    price=549.0,
                    pageviews=40,
                    units=3,
                    orders=0,
                    revenue=2100.0,
                    total_spend=150.0,
                    spend_sp=150.0,
                    spend_sb=0.0,
                    spend_sd=0.0,
                ),
                FlipkartProcessedDashboardData(
                    user=cls.user,
                    date=date(2026, 2, 11),
                    fsn="FSN-BETA",
                    portfolio="Planters",
                    category="Outdoor",
                    subcategory="Metal",
                    price=799.0,
                    pageviews=25,
                    units=1,
                    orders=0,
                    revenue=900.0,
                    total_spend=50.0,
                    spend_sp=50.0,
                    spend_sb=0.0,
                    spend_sd=0.0,
                ),
            ]
        )
        rebuild_daily_summary_for_user(cls.user)

    def test_summary_backed_payload_matches_raw_payload_for_core_analytics(self):
        filters = {
            "start_date": "2026-02-10",
            "end_date": "2026-02-11",
        }
        qs = ProcessedDashboardData.objects.filter(user=self.user)
        fk_qs = FlipkartProcessedDashboardData.objects.filter(user=self.user)

        summary_payload = run_orm_computation(
            qs,
            fk_qs,
            None,
            filters,
            self.user,
            cached_filter_metadata={"platforms": ["Amazon"], "dates": []},
            include_full_payload=False,
        )
        raw_payload = run_orm_computation(
            qs,
            fk_qs,
            None,
            filters,
            self.user,
            cached_filter_metadata={"platforms": ["Amazon"], "dates": []},
            include_full_payload=True,
        )

        for key in (
            "revenue",
            "orders",
            "units",
            "pageviews",
            "spend",
            "roas",
            "tacos",
            "active_asins",
            "mom_growth",
            "yoy_growth",
        ):
            self.assertAlmostEqual(
                summary_payload["kpis"][key],
                raw_payload["kpis"][key],
                places=2,
            )

        self.assertEqual(summary_payload["platforms"], raw_payload["platforms"])
        self.assertEqual(
            summary_payload["category_performance"],
            raw_payload["category_performance"],
        )
        self.assertEqual(
            summary_payload["cluster_performance"],
            raw_payload["cluster_performance"],
        )
        self.assertEqual(summary_payload["cat_top_products"], [])
        self.assertEqual(summary_payload["cat_under_products"], [])

    def test_summary_backed_charts_include_platform_trend_without_field_error(self):
        filters = {
            "start_date": "2026-02-10",
            "end_date": "2026-02-11",
        }
        payload = run_orm_computation(
            ProcessedDashboardData.objects.filter(user=self.user),
            FlipkartProcessedDashboardData.objects.filter(user=self.user),
            None,
            filters,
            self.user,
            cached_filter_metadata={"platforms": ["Amazon", "Flipkart"], "dates": []},
            include_full_payload=False,
        )

        trend = payload["charts"]["trend"]
        self.assertEqual(trend["labels"], ["2026-02-10", "2026-02-11"])
        self.assertEqual(trend["amazon_revenue"], [12000.0, 0.0])
        self.assertEqual(trend["flipkart_revenue"], [2100.0, 900.0])

    def test_ceo_visuals_summary_fast_path_skips_activity_metrics_without_key_error(self):
        payload = run_orm_computation(
            ProcessedDashboardData.objects.filter(user=self.user),
            FlipkartProcessedDashboardData.objects.filter(user=self.user),
            None,
            {"start_date": "2026-02-10", "end_date": "2026-02-11"},
            self.user,
            cached_filter_metadata={"platforms": ["Amazon", "Flipkart"], "dates": []},
            include_full_payload=False,
            section_scope="visuals",
            dashboard_view="ceo",
        )

        self.assertIn("charts", payload)
        self.assertEqual(payload["marketing"]["az_selling_sku_count"], 0)
        self.assertEqual(payload["marketing"]["fk_selling_sku_count"], 0)

    def test_monthly_activity_metrics_include_platform_split_keys(self):
        from apps.dashboard.services.asin_monthly_summary import (
            compute_activity_metrics_from_monthly,
        )

        DashboardAsinMonthlySummary.objects.bulk_create(
            [
                DashboardAsinMonthlySummary(
                    user=self.user,
                    platform="Amazon",
                    asin="ASIN-MONTHLY",
                    year_month=date(2026, 1, 1),
                    revenue=1000.0,
                    orders=1,
                    units=2,
                    pageviews=10,
                ),
                DashboardAsinMonthlySummary(
                    user=self.user,
                    platform="Flipkart",
                    asin="FSN-MONTHLY",
                    year_month=date(2026, 1, 1),
                    revenue=500.0,
                    orders=0,
                    units=0,
                    pageviews=15,
                ),
            ]
        )

        metrics = compute_activity_metrics_from_monthly(
            self.user,
            {"start_date": "2026-01-01", "end_date": "2026-02-20"},
        )

        self.assertEqual(metrics["az_selling_sku_count"], 1)
        self.assertEqual(metrics["fk_zero_selling_sku_count"], 1)
        self.assertEqual(metrics["zero_sales_pageviews"], 15)

    def test_summary_activity_counts_include_sales_file_rows_with_all_zero_metrics(self):
        SalesData.objects.create(
            user=self.user,
            date=date(2026, 2, 11),
            asin="ASIN-ZERO",
            pageviews=0,
            units=0,
            orders=0,
            revenue=0.0,
        )
        ProcessedDashboardData.objects.create(
            user=self.user,
            date=date(2026, 2, 11),
            asin="ASIN-ZERO",
            portfolio="Planters",
            category="Indoor",
            subcategory="Ceramic",
            price=299.0,
            pageviews=0,
            units=0,
            orders=0,
            revenue=0.0,
            spend_sp=0.0,
            spend_sb=0.0,
            spend_sd=0.0,
            total_spend=0.0,
        )

        payload = run_orm_computation(
            ProcessedDashboardData.objects.filter(user=self.user),
            FlipkartProcessedDashboardData.objects.filter(user=self.user),
            None,
            {"start_date": "2026-02-11", "end_date": "2026-02-11"},
            self.user,
            cached_filter_metadata={"platforms": ["Amazon", "Flipkart"], "dates": []},
            include_full_payload=False,
        )

        self.assertEqual(payload["marketing"]["zero_selling_sku_count"], 1)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dashboard-refresh-recovery-tests",
        }
    }
)
class DashboardRefreshRecoveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Users.objects.create(
            fname="Recovery",
            lname="Owner",
            email="recovery-owner@example.com",
            pswd="secret",
            cpswd="secret",
        )

    def _patch_refresh_side_effects(self):
        patches = [
            patch("apps.dashboard.services.daily_summary.rebuild_daily_summary_for_user"),
            patch("apps.dashboard.tasks.refresh_dashboard_inventory_summary_task"),
            patch("apps.dashboard.tasks.refresh_dashboard_asin_monthly_summary_task"),
            patch("apps.upload.tasks._enqueue_dashboard_warmup"),
            patch("celery.group"),
        ]
        started = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])

        inv_task_mock = started[1]
        asin_task_mock = started[2]
        celery_group_mock = started[4]

        inv_task_mock.si.return_value = Mock(name="inventory_sig")
        asin_task_mock.si.return_value = Mock(name="asin_sig")
        celery_group_result = Mock()
        celery_group_mock.return_value = celery_group_result
        return started

    def test_flipkart_category_refresh_rebuilds_when_processed_rows_are_missing(self):
        report_date = date(2026, 5, 1)
        FlipkartCategoryMap.objects.create(
            user=self.user,
            fsn="FSN-001",
            sku="SKU-001",
            portfolio="Planters",
            category="Indoor",
            subcategory="Ceramic",
        )
        FlipkartSearchTraffic.objects.create(
            user=self.user,
            fsn="FSN-001",
            sku="SKU-001",
            vertical="Pots",
            date=report_date,
            page_views=120,
            product_clicks=120,
            sales=8,
            revenue=6400.0,
        )
        FlipkartPLA.objects.create(
            user=self.user,
            campaign_id="CMP-001",
            fsn_id="FSN-001",
            date=report_date,
            ad_spend=450.0,
        )
        FlipkartPrice.objects.create(user=self.user, fsn="FSN-001", price=799.0)

        self._patch_refresh_side_effects()

        with patch("apps.upload.dashboard_builders.generate_flipkart_dashboard_data") as gen_fk, patch(
            "apps.upload.dashboard_builders.update_fk_category_in_processed_data"
        ) as upd_fk_cat:
            _run_dashboard_refresh(
                data_owner=self.user,
                user_id=self.user.id,
                is_flipkart=True,
                file_type="fk_category",
                affected_dates=[],
                dashboard_refreshed=False,
            )

        gen_fk.assert_called_once()
        self.assertEqual(gen_fk.call_args.args[0], self.user)
        self.assertIsNone(gen_fk.call_args.kwargs.get("only_dates"))
        self.assertIn("progress_callback", gen_fk.call_args.kwargs)
        upd_fk_cat.assert_not_called()

    def test_amazon_category_refresh_rebuilds_when_processed_rows_are_missing(self):
        report_date = date(2026, 5, 1)
        CategoryMapping.objects.create(
            user=self.user,
            asin="ASIN-001",
            portfolio="Planters",
            category="Indoor",
            subcategory="Ceramic",
        )
        PriceData.objects.create(user=self.user, asin="ASIN-001", price=599.0)
        SalesData.objects.create(
            user=self.user,
            date=report_date,
            asin="ASIN-001",
            pageviews=100,
            units=6,
            orders=5,
            revenue=5000.0,
        )
        SpendData.objects.create(
            user=self.user,
            date=report_date,
            asin="ASIN-001",
            ad_account="SP",
            ad_type="SP",
            spend=350.0,
        )

        self._patch_refresh_side_effects()

        with patch("apps.upload.dashboard_builders.generate_dashboard_data") as gen_amz, patch(
            "apps.upload.dashboard_builders.update_category_in_processed_data"
        ) as upd_amz_cat:
            _run_dashboard_refresh(
                data_owner=self.user,
                user_id=self.user.id,
                is_flipkart=False,
                file_type="category",
                affected_dates=[],
                dashboard_refreshed=False,
            )

        gen_amz.assert_called_once()
        self.assertEqual(gen_amz.call_args.args[0], self.user)
        self.assertIn("progress_callback", gen_amz.call_args.kwargs)
        upd_amz_cat.assert_not_called()

    def test_amazon_metadata_refresh_updates_only_uploaded_asins(self):
        ProcessedDashboardData.objects.create(
            user=self.user,
            date=date(2026, 5, 1),
            asin="ASIN-001",
            portfolio="Old",
            category="Old",
            subcategory="Old",
            price=499.0,
        )

        self._patch_refresh_side_effects()

        with patch("apps.upload.dashboard_builders.update_category_in_processed_data") as upd_amz_cat, patch(
            "apps.upload.dashboard_builders.update_price_in_processed_data"
        ) as upd_amz_price:
            _run_dashboard_refresh(
                data_owner=self.user,
                user_id=self.user.id,
                is_flipkart=False,
                file_type="category",
                affected_dates=[],
                metadata_file_types=["category", "price"],
                affected_entity_ids=["ASIN-001", "ASIN-002"],
                dashboard_refreshed=False,
            )

        upd_amz_cat.assert_called_once_with(self.user.id, asins=["ASIN-001", "ASIN-002"])
        upd_amz_price.assert_called_once_with(self.user.id, asins=["ASIN-001", "ASIN-002"])

    def test_flipkart_metadata_refresh_updates_only_uploaded_fsns(self):
        FlipkartCategoryMap.objects.create(
            user=self.user,
            fsn="FSN-001",
            sku="SKU-001",
            portfolio="Planters",
            category="Indoor",
            subcategory="Ceramic",
        )
        FlipkartSearchTraffic.objects.create(
            user=self.user,
            fsn="FSN-001",
            sku="SKU-001",
            vertical="Pots",
            date=date(2026, 5, 1),
            page_views=120,
            product_clicks=120,
            sales=8,
            revenue=6400.0,
        )
        FlipkartPLA.objects.create(
            user=self.user,
            campaign_id="CMP-001",
            fsn_id="FSN-001",
            date=date(2026, 5, 1),
            ad_spend=450.0,
        )
        FlipkartPrice.objects.create(user=self.user, fsn="FSN-001", price=799.0)
        FlipkartProcessedDashboardData.objects.create(
            user=self.user,
            date=date(2026, 5, 1),
            fsn="FSN-001",
            portfolio="Old",
            category="Old",
            subcategory="Old",
            price=499.0,
        )

        self._patch_refresh_side_effects()

        with patch("apps.upload.dashboard_builders.update_fk_category_in_processed_data") as upd_fk_cat, patch(
            "apps.upload.dashboard_builders.update_fk_price_in_processed_data"
        ) as upd_fk_price:
            _run_dashboard_refresh(
                data_owner=self.user,
                user_id=self.user.id,
                is_flipkart=True,
                file_type="fk_category",
                affected_dates=[],
                metadata_file_types=["fk_category", "fk_price"],
                affected_entity_ids=["FSN-001", "FSN-002"],
                dashboard_refreshed=False,
            )

        upd_fk_cat.assert_called_once_with(self.user.id, fsns=["FSN-001", "FSN-002"])
        upd_fk_price.assert_called_once_with(self.user.id, fsns=["FSN-001", "FSN-002"])

    def test_inventory_summary_task_skips_duplicate_run_when_lock_is_held(self):
        cache.set(
            f"dashboard_inventory_summary_task_lock_{self.user.id}",
            "1",
            timeout=300,
        )

        with patch("apps.dashboard.tasks.rebuild_inventory_summary_for_user") as rebuild_mock:
            result = refresh_dashboard_inventory_summary_task(self.user.id, only_dates=[])

        self.assertEqual(result, {"rows_written": 0, "skipped": "duplicate-run"})
        rebuild_mock.assert_not_called()


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dashboard-upload-batch-tests",
        }
    }
)
class DashboardUploadBatchFinalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = Users.objects.create(
            fname="Batch",
            lname="Owner",
            email="batch-owner@example.com",
            pswd="secret",
            cpswd="secret",
        )

    def setUp(self):
        cache.clear()

    def test_amazon_mixed_batch_with_category_and_sales_rebuilds_only_affected_dates(self):
        batch_id = "mixedbatch01"
        _register_upload_batch(
            batch_id,
            batch_total=2,
            user_id=self.user.id,
            data_owner_id=self.user.id,
            is_flipkart=False,
        )

        with patch("apps.upload.tasks._send_ws"), patch(
            "apps.upload.tasks.refresh_dashboard_after_upload_task.delay"
        ) as refresh_delay:
            _mark_batch_task_complete(
                batch_id=batch_id,
                batch_total=2,
                user_id=self.user.id,
                data_owner_id=self.user.id,
                is_flipkart=False,
                success=True,
                file_type="sales",
                affected_dates=["2026-05-20"],
            )
            _mark_batch_task_complete(
                batch_id=batch_id,
                batch_total=2,
                user_id=self.user.id,
                data_owner_id=self.user.id,
                is_flipkart=False,
                success=True,
                file_type="category",
                affected_dates=[],
            )

        refresh_delay.assert_called_once_with(
            data_owner_id=self.user.id,
            user_id=self.user.id,
            is_flipkart=False,
            file_type="sales",
            affected_dates=["2026-05-20"],
            metadata_file_types=[],
            affected_entity_ids=[],
            dashboard_refreshed=False,
        )

    def test_amazon_metadata_only_batch_refreshes_category_and_price_for_uploaded_asins(self):
        batch_id = "metabatch01"
        _register_upload_batch(
            batch_id,
            batch_total=2,
            user_id=self.user.id,
            data_owner_id=self.user.id,
            is_flipkart=False,
        )

        with patch("apps.upload.tasks._send_ws"), patch(
            "apps.upload.tasks.refresh_dashboard_after_upload_task.delay"
        ) as refresh_delay:
            _mark_batch_task_complete(
                batch_id=batch_id,
                batch_total=2,
                user_id=self.user.id,
                data_owner_id=self.user.id,
                is_flipkart=False,
                success=True,
                file_type="category",
                affected_dates=[],
                affected_entity_ids=["ASIN-001"],
            )
            _mark_batch_task_complete(
                batch_id=batch_id,
                batch_total=2,
                user_id=self.user.id,
                data_owner_id=self.user.id,
                is_flipkart=False,
                success=True,
                file_type="price",
                affected_dates=[],
                affected_entity_ids=["ASIN-002"],
            )

        refresh_delay.assert_called_once_with(
            data_owner_id=self.user.id,
            user_id=self.user.id,
            is_flipkart=False,
            file_type="category",
            affected_dates=[],
            metadata_file_types=["category", "price"],
            affected_entity_ids=["ASIN-001", "ASIN-002"],
            dashboard_refreshed=False,
        )
