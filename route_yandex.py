import urllib.parse
import requests
from config import GEOCODER_API_KEY


def geocode(address: str):
    """Получает координаты (lat, lon) через Яндекс.Геокодер."""
    url = "https://geocode-maps.yandex.ru/1.x/"
    params = {
        "apikey": GEOCODER_API_KEY,
        "geocode": address,
        "format": "json",
        "results": 1,
        "lang": "ru_RU",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    
    members = r.json()["response"]["GeoObjectCollection"]["featureMember"]
    if not members:
        raise ValueError(f"Не найден адрес: {address}")
    
    lon, lat = map(float, members[0]["GeoObject"]["Point"]["pos"].split())
    return lat, lon

def get_coordinates_url(addresses: list):
    parsed_addresses = []
    
    for address_str in addresses:
        try:
            lat, lon = geocode(address_str)
            parsed_addresses.append({
                "address": address_str,
                "lat": lat,
                "lon": lon
            })
        except Exception as e:
            print(f"Ошибка геокодинга адреса '{address_str}': {e}")
            # Можно либо пропустить адрес, либо прервать выполнение
            continue 

    if len(parsed_addresses) < 2:
        return "Не удалось построить маршрут (недостаточно корректных адресов)"

    # Формируем строку маршрута (ВАЖНО: lat,lon!)
    rtext = "~".join(f"{a['lat']:.6f},{a['lon']:.6f}" for a in parsed_addresses)

    # Центр карты (среднее арифметическое) и зум
    avg_lon = sum(a["lon"] for a in parsed_addresses) / len(parsed_addresses)
    avg_lat = sum(a["lat"] for a in parsed_addresses) / len(parsed_addresses)

    url = "https://yandex.ru/maps/?" + urllib.parse.urlencode({
        "rtext": rtext,
        "rtt": "auto",
        "ll": f"{avg_lon:.6f},{avg_lat:.6f}",
        "z": "12",
    }, safe="~,:")
    
    return url