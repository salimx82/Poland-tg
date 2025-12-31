import os
import re
import logging
import asyncio
import cv2
import numpy as np
from PIL import Image
from io import BytesIO
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# লগিং কনফিগারেশন
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# টেলিগ্রাম বট টোকেন (Render Environment Variable থেকে নিবে)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
    raise ValueError("Please set TELEGRAM_BOT_TOKEN environment variable")

def extract_polish_numbers(text):
    """পোল্যান্ডের ফোন নম্বর এক্সট্র্যাক্ট করুন"""
    if not text:
        return []
    
    patterns = [
        r'\+48\s?\d{3}\s?\d{3}\s?\d{3}',
        r'48\s?\d{3}\s?\d{3}\s?\d{3}',
        r'\d{3}[\s\-]?\d{3}[\s\-]?\d{3}',
        r'\(\d{2}\)\s?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}',
        r'\+48\d{9}',
        r'48\d{9}',
        r'\d{9}',
    ]
    
    found_numbers = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        found_numbers.extend(matches)
    
    # ডুপ্লিকেট রিমুভ এবং ফরম্যাট
    unique_numbers = []
    seen = set()
    
    for num in found_numbers:
        digits = re.sub(r'\D', '', num)
        
        if digits.startswith('48'):
            digits = digits[2:]
        
        if len(digits) == 9 and digits not in seen:
            seen.add(digits)
            formatted = f"+48{digits}"
            unique_numbers.append(formatted)
    
    return unique_numbers

def process_image(image_data):
    """ইমেজ প্রসেস করে টেক্সট এক্সট্র্যাক্ট করুন"""
    try:
        # Tesseract ইমপোর্ট
        try:
            import pytesseract
        except ImportError:
            logger.error("pytesseract not installed")
            return None
        
        image = Image.open(BytesIO(image_data))
        img_array = np.array(image)
        
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array
        
        # ইমেজ প্রিপ্রসেসিং
        gray = cv2.medianBlur(gray, 3)
        gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(gray, config=custom_config, lang='eng')
        
        return text
    except Exception as e:
        logger.error(f"Error processing image: {e}")
        return None

async def delete_messages_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list):
    """৫ মিনিট পর মেসেজ ডিলিট করুন"""
    await asyncio.sleep(300)
    
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.error(f"Error deleting message {msg_id}: {e}")

async def copy_all_numbers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """সব নাম্বার কপি করার ক্যালব্যাক হ্যান্ডলার"""
    query = update.callback_query
    await query.answer()
    
    message_text = query.message.text
    numbers = []
    
    for line in message_text.split('\n'):
        line = line.strip()
        if line.startswith('+48') and len(line) == 12:
            numbers.append(line)
    
    if numbers:
        all_numbers_text = '\n'.join(numbers)
        
        confirmation_msg = await query.message.reply_text(
            f"✅ {len(numbers)}টি নাম্বার কপি করা হয়েছে!\n\n"
            f"📋 এখন পেস্ট করতে পারেন।"
        )
        
        asyncio.create_task(
            delete_messages_after_delay(context, query.message.chat_id, [confirmation_msg.message_id])
        )
    else:
        await query.answer("❌ কপি করতে সমস্যা হয়েছে", show_alert=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """স্টার্ট কমান্ড হ্যান্ডলার"""
    start_msg = await update.message.reply_text(
        "📸 ইমেজ পাঠান, আমি পোল্যান্ডের ফোন নম্বর স্ক্যান করে দেব।\n\n"
        "✅ নাম্বারগুলো কপি করতে নিচের 'কপি' বাটনে ক্লিক করুন।"
    )
    
    context.job_queue.run_once(
        lambda ctx: delete_messages_after_delay(ctx, update.message.chat_id, 
                                               [start_msg.message_id, update.message.message_id]),
        300
    )

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ইমেজ মেসেজ হ্যান্ডলার"""
    try:
        chat_id = update.message.chat_id
        user_message_id = update.message.message_id
        
        # ইমেজ সাথে সাথে ডিলিট করুন
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user_message_id)
        except Exception as e:
            logger.error(f"Error deleting image: {e}")
        
        # ইমেজ ডাউনলোড করুন
        photo_file = await update.message.photo[-1].get_file()
        image_data = await photo_file.download_as_bytearray()
        
        # ইমেজ প্রসেস করুন
        text = process_image(image_data)
        
        if not text:
            error_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="❌"
            )
            context.job_queue.run_once(
                lambda ctx: delete_messages_after_delay(ctx, chat_id, [error_msg.message_id]),
                30
            )
            return
        
        # পোল্যান্ডের নম্বর এক্সট্র্যাক্ট করুন
        polish_numbers = extract_polish_numbers(text)
        
        if not polish_numbers:
            no_numbers_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="❌"
            )
            context.job_queue.run_once(
                lambda ctx: delete_messages_after_delay(ctx, chat_id, [no_numbers_msg.message_id]),
                30
            )
            return
        
        # শুধু নাম্বারগুলোর লিস্ট তৈরি করুন
        result_text = ""
        for number in polish_numbers:
            result_text += f"{number}\n"
        
        result_text = result_text.strip()
        
        # কপি বাটন তৈরি করুন
        keyboard = [
            [InlineKeyboardButton("📋 সব নাম্বার কপি করুন", callback_data="copy_all")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # রেজাল্ট পাঠান
        result_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=reply_markup
        )
        
        # ৫ মিনিট পর এই মেসেজ ডিলিট করুন
        context.job_queue.run_once(
            lambda ctx: delete_messages_after_delay(ctx, chat_id, [result_msg.message_id]),
            300
        )
            
    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            error_msg = await context.bot.send_message(
                chat_id=chat_id,
                text="❌"
            )
            context.job_queue.run_once(
                lambda ctx: delete_messages_after_delay(ctx, chat_id, [error_msg.message_id]),
                30
            )
        except:
            pass

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """টেক্সট মেসেজ হ্যান্ডলার"""
    chat_id = update.message.chat_id
    user_message_id = update.message.message_id
    
    text = update.message.text
    
    # পোল্যান্ডের নম্বর এক্সট্র্যাক্ট করুন
    polish_numbers = extract_polish_numbers(text)
    
    if polish_numbers:
        # ইউজারের মেসেজ ডিলিট করুন
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user_message_id)
        except:
            pass
        
        # শুধু নাম্বারগুলোর লিস্ট তৈরি করুন
        result_text = ""
        for number in polish_numbers:
            result_text += f"{number}\n"
        
        result_text = result_text.strip()
        
        # কপি বাটন তৈরি করুন
        keyboard = [
            [InlineKeyboardButton("📋 সব নাম্বার কপি করুন", callback_data="copy_all")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # রেজাল্ট পাঠান
        result_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            reply_markup=reply_markup
        )
        
        # ৫ মিনিট পর এই মেসেজ ডিলিট করুন
        context.job_queue.run_once(
            lambda ctx: delete_messages_after_delay(ctx, chat_id, [result_msg.message_id]),
            300
        )
    else:
        # যদি নাম্বার না থাকে, মেসেজ ডিলিট করুন
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user_message_id)
        except:
            pass

async def health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """হেলথ চেক কমান্ড"""
    await update.message.reply_text("🤖 বট চলমান...")

def main():
    """মেইন ফাংশন"""
    # অ্যাপ্লিকেশন ক্রিয়েট করুন
    application = Application.builder().token(TOKEN).build()
    
    # হ্যান্ডলার যোগ করুন
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("health", health_check))
    application.add_handler(CallbackQueryHandler(copy_all_numbers_callback, pattern="^copy_all$"))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # বট শুরু করুন
    logger.info("🤖 Polish Number Scanner Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()
