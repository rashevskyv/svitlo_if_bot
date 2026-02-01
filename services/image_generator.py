import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Кольори для графіків
COLOR_ON = "#4CAF50"      # Green
COLOR_OFF = "#D32F2F"     # Red (Darker)
COLOR_POSSIBLE = "#81D4FA" # Light Blue
COLOR_UNKNOWN = "#9E9E9E"  # Grey
COLOR_TEXT_WHITE = "#FFFFFF"
COLOR_ACCENT = "#FF6D00"   # Orange for headers

def generate_schedule_image(
    today_half: List[str], 
    tomorrow_half: List[str], 
    current_dt: datetime, 
    mode: str = "classic",
    queue_id: str = "Unknown",
    show_time_marker: bool = True,
    region_name: Optional[str] = None,
    bot_username: Optional[str] = None
) -> List[BytesIO]:
    """
    Головна функція генерації зображень залежно від режиму.
    Повертає список буферів (сьогодні, завтра).
    """
    images = []
    
    if mode == "list":
        images.append(_generate_list_view(today_half, current_dt, queue_id, "Сьогодні", show_time_marker, region_name, bot_username))
        if tomorrow_half and len(tomorrow_half) == 48:
            tomorrow_dt = current_dt + timedelta(days=1)
            images.append(_generate_list_view(tomorrow_half, tomorrow_dt, queue_id, "Завтра", show_time_marker, region_name, bot_username))
    elif mode == "dynamic":
        # Динамічний режим за своєю суттю об'єднує 24 години від зараз
        images.append(_generate_circle_view(today_half, tomorrow_half, current_dt, queue_id, dynamic=True, show_time_marker=True, region_name=region_name, bot_username=bot_username))
    else:
        # Класичне коло - два окремих зображення
        images.append(_generate_circle_view(today_half, [], current_dt, queue_id, dynamic=False, title="Сьогодні", show_time_marker=show_time_marker, region_name=region_name, bot_username=bot_username))
        if tomorrow_half and len(tomorrow_half) == 48:
            tomorrow_dt = current_dt + timedelta(days=1)
            images.append(_generate_circle_view(tomorrow_half, [], tomorrow_dt, queue_id, dynamic=False, title="Завтра", show_time_marker=show_time_marker, region_name=region_name, bot_username=bot_username))
            
    return images

def _generate_circle_view(
    day_data: List[str], 
    tomorrow_data: List[str], # Тільки для dynamic=True
    current_dt: datetime, 
    queue_id: str,
    dynamic: bool = False,
    title: str = "Сьогодні",
    show_time_marker: bool = True,
    region_name: Optional[str] = None,
    bot_username: Optional[str] = None
) -> BytesIO:
    """
    Генерує одну кругову діаграму.
    """
    if dynamic:
        current_idx = current_dt.hour * 2 + (1 if current_dt.minute >= 30 else 0)
        # Сектори від зараз до кінця дня (сьогодні)
        today_part = day_data[current_idx:]
        # Сектори від 00:00 до зараз (завтра)
        if tomorrow_data and len(tomorrow_data) == 48:
            tomorrow_part = tomorrow_data[:current_idx]
            waiting_tomorrow = False
        else:
            tomorrow_part = ["unknown"] * current_idx
            waiting_tomorrow = True
            
        display_data = (tomorrow_part + today_part + ["unknown"] * 48)[:48]
        title = "Прогноз (24 год)"
    else:
        display_data = (day_data + ["unknown"] * 48)[:48]
        waiting_tomorrow = False

    color_map = {
        "on": COLOR_ON, 
        "off": COLOR_OFF, 
        "possible": COLOR_POSSIBLE,
        "unknown": COLOR_UNKNOWN
    }
    colors = [color_map.get(s, COLOR_UNKNOWN) for s in display_data]
    sizes = [1] * 48

    fig = plt.figure(figsize=(8, 8))
    # Максимально збільшуємо графік, щоб він займав майже весь простір
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98], projection=None, aspect='equal')
    
    # Малюємо кільце з 48 сегментів
    ax.pie(sizes, colors=colors, startangle=90, counterclock=False, 
           wedgeprops=dict(width=0.4, edgecolor='none', linewidth=0))
    
    # Встановлюємо межі осей явно, щоб зовнішні дуги не обрізалися
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)

    # Додаємо розділювачі годин вручну (тільки 24 лінії, кожні 2 сегменти)
    for i in range(24):
        angle = 90 - i * 15
        r_in, r_out = 0.6, 1.0
        x_in = r_in * np.cos(np.radians(angle))
        y_in = r_in * np.sin(np.radians(angle))
        x_out = r_out * np.cos(np.radians(angle))
        y_out = r_out * np.sin(np.radians(angle))
        ax.plot([x_in, x_out], [y_in, y_out], color='w', linewidth=0.8, zorder=3)

    # Додаємо цифри годин (всі 24 години)
    for i in range(24):
        angle = 90 - (i * 15 + 7.5) 
        r = 0.8 
        x = r * np.cos(np.radians(angle))
        y = r * np.sin(np.radians(angle))
        ax.text(x, y, f"{i:02d}", ha='center', va='center', 
                fontsize=11, fontweight='bold', color=COLOR_TEXT_WHITE)

    # ЛОГІКА НАКЛАДАННЯ (ДРУГИЙ ШАР ДЛЯ ПЕРЕКРИТИХ ВІДКЛЮЧЕНЬ)
    if dynamic:
        current_idx = current_dt.hour * 2 + (1 if current_dt.minute >= 30 else 0)
        
        # 1. Зовнішня дуга: Завтрашні відключення, що зараз перекриті Сьогоднішнім днем
        if tomorrow_data and len(tomorrow_data) == 48:
            for i in range(current_idx, 48):
                t_status = tomorrow_data[i]
                if t_status in ["off", "possible"]:
                    angle_start = 90 - (i * 7.5)
                    angle_end = 90 - ((i + 1) * 7.5)
                    color = color_map.get(t_status, COLOR_UNKNOWN)
                    # Трішки товстіша і з вираженим відступом
                    p = patches.Wedge((0, 0), 1.08, angle_end, angle_start, width=0.06, 
                                      color=color, alpha=1.0, zorder=10)
                    ax.add_patch(p)
        
        # 2. Внутрішня дуга: Сьогоднішні відключення, що зараз перекриті Завтрашнім днем (минуле)
        if day_data and len(day_data) == 48:
            for i in range(0, current_idx):
                d_status = day_data[i]
                if d_status in ["off", "possible"]:
                    angle_start = 90 - (i * 7.5)
                    angle_end = 90 - ((i + 1) * 7.5)
                    color = color_map.get(d_status, COLOR_UNKNOWN)
                    # Малюємо внутрішній шар (під основним кільцем)
                    p = patches.Wedge((0, 0), 0.58, angle_end, angle_start, width=0.06, 
                                      color=color, alpha=1.0, zorder=10)
                    ax.add_patch(p)

    # Стрілка поточного часу
    if show_time_marker:
        current_angle = 90 - (current_dt.hour * 15 + current_dt.minute * 0.25)
        r_start, r_end = 0.55, 1.05
        x_s = r_start * np.cos(np.radians(current_angle))
        y_s = r_start * np.sin(np.radians(current_angle))
        x_e = r_end * np.cos(np.radians(current_angle))
        y_e = r_end * np.sin(np.radians(current_angle))
        ax.plot([x_s, x_e], [y_s, y_e], color='#2196F3', linewidth=4, solid_capstyle='round', zorder=5)
        ax.scatter([x_e], [y_e], color='#2196F3', s=100, edgecolors='white', linewidth=2, zorder=6)

    # Розділювач опівночі (00:00) - завжди вгорі
    mx_s, mx_e = 0.55, 1.05
    ax.plot([0, 0], [mx_s, mx_e], color='white', linewidth=4, zorder=10)
    
    # Центр
    ax.text(0, 0.15, queue_id, ha='center', va='center', fontsize=20, fontweight='bold')
    
    
    ax.text(0, -0.1, title, ha='center', va='center', fontsize=14, fontweight='bold', color='#555555')
    ax.text(0, -0.25, f"{current_dt.strftime('%d.%m.%Y')}", ha='center', va='center', fontsize=10, color='grey')

    if region_name:
        # Додаємо заголовок зверху (трохи вище, щоб не наповзало)
        plt.text(0.5, 0.99, region_name, ha='center', va='top', fontsize=16, fontweight='bold', color='#333333', transform=fig.transFigure, 
                 bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=2))
    
    if bot_username:
        # Переносимо в самий нижній кут з невеликою підкладкою для читабельності
        plt.text(0.99, 0.01, f"@{bot_username.replace('@', '')}", ha='right', va='bottom', fontsize=9, color='grey', transform=fig.transFigure,
                 bbox=dict(facecolor='white', alpha=0.5, edgecolor='none', pad=1))

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    plt.close(fig)
    return buf

def _generate_list_view(
    half_list: List[str], 
    current_dt: datetime, 
    queue_id: str,
    title: str = "Сьогодні",
    show_time_marker: bool = True,
    region_name: Optional[str] = None,
    bot_username: Optional[str] = None
) -> BytesIO:
    """
    Генерує текстову картку зі списком відключень для одного дня.
    """
    intervals = []
    start_time = None
    for i, status in enumerate(half_list):
        if status == "off" and start_time is None:
            start_time = i
        elif status != "off" and start_time is not None:
            intervals.append((start_time, i))
            start_time = None
    if start_time is not None:
        intervals.append((start_time, 48))

    # Розрахунок висоти (завжди квадрат 8x8)
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_axes([0.05, 0.05, 0.9, 0.9])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()

    # Заголовок регіону (самий верх)
    if region_name:
        plt.text(0.5, 0.97, region_name, ha='center', va='top', fontsize=16, fontweight='bold', color='#333333', transform=ax.transAxes)
    
    # Підзаголовок
    plt.text(0.5, 0.90, f"Графік відключень • {title}", ha='center', va='top', fontsize=14, fontweight='bold', color='#555555', transform=ax.transAxes)
    
    y_pos = 0.65 # Починаємо нижче, щоб не наповзало на заголовок
    
    if not intervals:
        plt.text(0.5, 0.45, f"{current_dt.strftime('%d.%m.%Y')}\nВідключень не заплановано", 
                 ha='center', va='center', fontsize=16, color='green', fontweight='bold', transform=ax.transAxes)
    else:
        # Дата (вище першого інтервалу)
        plt.text(0.05, y_pos + 0.12, f"{current_dt.strftime('%d.%m.%Y')}", fontsize=14, fontweight='bold', color='#333333', transform=ax.transAxes)
        
        # Динамічний крок залежно від кількості інтервалів
        step = min(0.14, 0.50 / len(intervals)) if intervals else 0.15
        
        for start, end in intervals:
            s_h, s_m = divmod(start * 30, 60)
            e_h, e_m = divmod(end * 30, 60)
            if e_h == 24: e_h, e_m = 0, 0
            
            duration_min = (end - start) * 30
            dur_h, dur_m = divmod(duration_min, 60)
            dur_str = f"{dur_h} год" + (f" {dur_m} хв" if dur_m else "")

            # Динамічний розмір плашки та шрифту
            box_h = min(0.12, 0.5 / len(intervals)) if intervals else 0.08
            font_s = 18 if len(intervals) <= 3 else 16
            
            # Плашка інтервалу
            rect = patches.FancyBboxPatch((0.05, y_pos - box_h/2), 0.6, box_h, 
                                          facecolor='#C2185B', edgecolor='none', 
                                          boxstyle='round,pad=0.02', transform=ax.transAxes)
            ax.add_patch(rect)
            
            plt.text(0.15, y_pos, f"{s_h:02d}:{s_m:02d}", color='white', fontsize=font_s, fontweight='bold', ha='center', va='center', transform=ax.transAxes)
            plt.text(0.35, y_pos, "———", color='white', fontsize=font_s, ha='center', va='center', transform=ax.transAxes)
            plt.text(0.55, y_pos, f"{e_h:02d}:{e_m:02d}", color='white', fontsize=font_s, fontweight='bold', ha='center', va='center', transform=ax.transAxes)
            
            # Тривалість
            plt.text(0.75, y_pos, dur_str, color='#C2185B', fontsize=font_s - 2, fontweight='bold', 
                     ha='left', va='center', bbox=dict(facecolor='white', edgecolor='#C2185B', boxstyle='round,pad=0.3'), transform=ax.transAxes)
            
            y_pos -= step * 1.2 if len(intervals) <= 3 else step
            
    # Номер черги в нижньому правому куті (піднято, щоб не заважати тегу)
    plt.text(0.95, 0.12, f"{queue_id}", ha='right', va='bottom', fontsize=16, fontweight='bold', 
             bbox=dict(facecolor=COLOR_ACCENT, alpha=0.8, edgecolor='none', boxstyle='round,pad=0.5'), color='white',
             transform=ax.transAxes)

    if show_time_marker:
        plt.text(0.01, 0.01, f"Станом на {current_dt.strftime('%H:%M')}", fontsize=9, color='grey', transform=ax.transAxes, ha='left', va='bottom')

    if bot_username:
        # Вирівнюємо по горизонталі з "Станом на"
        plt.text(0.99, 0.01, f"@{bot_username.replace('@', '')}", ha='right', va='bottom', fontsize=9, color='grey', transform=ax.transAxes,
                 bbox=dict(facecolor='white', alpha=0.5, edgecolor='none', pad=1))

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    plt.close(fig)
    return buf

def convert_api_to_half_list(day_schedule: dict) -> List[str]:
    """
    Перетворює словник API {"00:00": 1, ...} у список з 48 елементів.
    """
    res = []
    for h in range(24):
        for m in (0, 30):
            label = f"{h:02d}:{m:02d}"
            code = day_schedule.get(label, 0)
            if code == 1: res.append("on")
            elif code == 2: res.append("off")
            elif code == 3: res.append("possible")
            else: res.append("unknown")
    return res

def is_schedule_empty(half_list: List[str]) -> bool:
    """
    Перевіряє, чи є графік порожнім (тільки невідомі статуси або тільки "є світло").
    Повністю зелений графік на завтра зазвичай означає відсутність даних.
    """
    if not half_list: return True
    return all(s == "unknown" for s in half_list) or all(s == "on" for s in half_list)

def get_next_event_info(today_half: List[str], tomorrow_half: List[str], current_dt: datetime) -> str:
    """
    Повертає текстовий прогноз та статистику на сьогодні та завтра.
    """
    def calc_stats(half_list):
        off_count = half_list.count("off")
        hours = off_count / 2
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h} год" + (f" {m} хв" if m else "")

    def get_intervals(half_list):
        intervals = []
        start_time = None
        for i, status in enumerate(half_list):
            if status in ["off", "possible"] and start_time is None:
                start_time = i
            elif status not in ["off", "possible"] and start_time is not None:
                intervals.append((start_time, i))
                start_time = None
        if start_time is not None:
            intervals.append((start_time, len(half_list)))
        
        formatted = []
        for s, e in intervals:
            s_h, s_m = divmod(s * 30, 60)
            e_h, e_m = divmod(e * 30, 60)
            if e_h == 24: e_h, e_m = 0, 0
            formatted.append(f"{s_h:02d}:{s_m:02d}–{e_h:02d}:{e_m:02d}")
        return formatted

    today_intervals = get_intervals(today_half)
    today_stats = calc_stats(today_half)
    
    # Пошук наступної події
    current_idx = current_dt.hour * 2 + (1 if current_dt.minute >= 30 else 0)
    combined = today_half + tomorrow_half
    
    next_event_idx = -1
    current_status = today_half[current_idx] if current_idx < 48 else "unknown"
    
    for i in range(current_idx + 1, len(combined)):
        if combined[i] != current_status and combined[i] != "unknown":
            next_event_idx = i
            break
            
    if next_event_idx == -1:
        if current_status == "off":
            forecast = "⚡️ Змін у графіку поки не заплановано."
        else:
            # Перевіряємо чи були відключення сьогодні взагалі
            if any(s in ["off", "possible"] for s in today_half):
                forecast = "⚡️ Відключень на сьогодні більше не заплановано."
            else:
                forecast = "⚡️ Відключень на сьогодні не заплановано."
    else:
        event_time = datetime.combine(current_dt.date(), datetime.min.time()) + timedelta(minutes=next_event_idx * 30)
        diff = event_time - current_dt
        diff_h, diff_m = divmod(int(diff.total_seconds() // 60), 60)
        
        time_str = event_time.strftime("%H:%M")
        if next_event_idx >= 48:
            time_str += " (завтра)"
            
        if combined[next_event_idx] == "off":
            action = "відключення"
        elif combined[next_event_idx] == "possible":
            action = "можливе відключення"
        else:
            action = "відновлення світла"
        
        # Розрахунок тривалості наступного стану
        duration_idx = 0
        for j in range(next_event_idx + 1, len(combined)):
            if combined[j] == combined[next_event_idx]:
                duration_idx += 1
            else:
                break
        duration_idx += 1 # Включаємо сам сектор початку
        dur_h, dur_m = divmod(duration_idx * 30, 60)
        dur_str = f"{dur_h}г" + (f" {dur_m}хв" if dur_m else "")
        
        forecast = f"🕒 Наступне **{action}**: о **{time_str}**\n⏳ Залишилось: **{diff_h}г {diff_m}хв**\n📏 Тривалість: **{dur_str}**"

    res = f"{forecast}\n\n"
    
    # Списки інтервалів
    if today_intervals:
        res += f"🔴 **Відключення сьогодні:**\n{', '.join(today_intervals)}\n"
    else:
        res += "🟢 **Сьогодні без відключень**\n"
        
    if tomorrow_half and len(tomorrow_half) == 48:
        tomorrow_intervals = get_intervals(tomorrow_half)
        if tomorrow_intervals:
            res += f"🔴 **Відключення завтра:**\n{', '.join(tomorrow_intervals)}\n"
        else:
            res += "🟢 **Завтра без відключень**\n"

    res += f"\n📊 **Статистика відключень:**\n• Сьогодні: **{today_stats}**"
    
    if tomorrow_half and len(tomorrow_half) == 48:
        tomorrow_stats = calc_stats(tomorrow_half)
        res += f"\n• Завтра: **{tomorrow_stats}**"
        
    return res
