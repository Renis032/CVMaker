"""City and country words used to recognise where a job is located."""

COUNTRY_TERMS = {
    "BG": ["bulgaria", "sofia", "plovdiv", "varna", "burgas", "ruse", "stara zagora", ", bg"],
    "DE": ["germany", "deutschland", "berlin", "munich", "münchen", "frankfurt", "hamburg", "cologne", ", de"],
    "NL": ["netherlands", "amsterdam", "rotterdam", "utrecht", "eindhoven", ", nl"],
    "GB": ["united kingdom", "uk", "london", "manchester", "edinburgh", ", gb"],
    "AT": ["austria", "vienna", "wien", "graz", ", at"],
    "RO": ["romania", "bucharest", "cluj", "iasi", ", ro"],
    "PL": ["poland", "warsaw", "krakow", "kraków", "wroclaw", ", pl"],
    "ES": ["spain", "madrid", "barcelona", "valencia", ", es"],
}


def country_name(code: str) -> str:
    terms = COUNTRY_TERMS.get(code.upper())
    return terms[0].title() if terms else code
