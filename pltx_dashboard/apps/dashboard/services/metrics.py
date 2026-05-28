GST_REVENUE_FACTOR = 0.7


def gst_adjusted_revenue(revenue):
    return float(revenue or 0) * GST_REVENUE_FACTOR


def safe_divide(numerator, denominator, default=0):
    denominator = float(denominator or 0)
    if denominator == 0:
        return default
    return float(numerator or 0) / denominator


def safe_growth(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)
    if previous == 0:
        if current == 0:
            return 0
        return 100.0 if current > 0 else -100.0
    return round((current - previous) / abs(previous) * 100, 1)


def roas(revenue, spend):
    return safe_divide(gst_adjusted_revenue(revenue), spend)


def tacos(revenue, spend):
    adjusted_revenue = gst_adjusted_revenue(revenue)
    return safe_divide(spend, adjusted_revenue) * 100 if adjusted_revenue else 0


def amazon_cvr(orders, pageviews):
    return safe_divide(orders, pageviews) * 100


def flipkart_cvr(units, pageviews):
    return safe_divide(units, pageviews) * 100
