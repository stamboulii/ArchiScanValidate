"""
Text Extractor - Extraction texte brut (PyMuPDF + OCR fallback)
"""

import re
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TextExtractor:

    def __init__(self, use_ocr: bool = True, tesseract_path: Optional[str] = None):
        self.use_ocr = use_ocr
        self.tesseract_path = tesseract_path

    def extract(self, pdf_path: str, page_num: Optional[int] = None, force_ocr: bool = False) -> dict:
        """
        Returns dict:
            text_pymupdf, text_ocr, primary_source, pages_data, ocr_pages_data
        
        Args:
            pdf_path: Chemin vers le fichier PDF
            page_num: Numero de page (0-indexed) ou None pour toutes les pages
        """
        path = Path(pdf_path)
        result = {
            "text_pymupdf": "",
            "raw_pymupdf": "",      # texte brut AVANT nettoyage (lignes préservées)
            "text_ocr": "",
            "primary_source": "pymupdf",
            "pages_data": [],
            "ocr_pages_data": [],
        }

        if not path.exists():
            logger.error(f"Fichier non trouve: {pdf_path}")
            return result

        # Etape 1: PyMuPDF
        raw_text, pages_data = self._extract_pymupdf(path, page_num=page_num)
        result["raw_pymupdf"] = raw_text                     # brut, lignes préservées
        result["text_pymupdf"] = self._clean_text(raw_text)  # nettoyé pour regex
        result["pages_data"] = pages_data

        # Etape 2: OCR si peu de texte OU forcé (e.g. maison multi-page)
        has_enough = len(result["text_pymupdf"].strip()) > 50
        surface_count = len(re.findall(r"\d+[\.,]\d+\s*m[²2]", result["text_pymupdf"]))

        if self.use_ocr and (force_ocr or not has_enough or surface_count < 3):
            logger.info("OCR activé%s", " (forcé)" if force_ocr else " (texte PyMuPDF insuffisant)")
            # Extraire l'OCR avec donnees structurelles pour spatial
            text_ocr, ocr_pages_data = self._extract_ocr_with_data(path, page_num=page_num)
            result["text_ocr"] = text_ocr
            result["ocr_pages_data"] = ocr_pages_data
            
            # TOUJOURS preferer OCR si disponible et active
            if text_ocr and len(text_ocr.strip()) > 20:
                result["primary_source"] = "ocr"

        return result

    def _extract_pymupdf(self, path: Path, page_num: Optional[int] = None) -> tuple:
        """Texte + données structurelles pour spatial_extractor
        
        Args:
            path: Chemin vers le fichier PDF
            page_num: Numero de page (0-indexed) ou None pour toutes les pages
        """
        try:
            import fitz
            doc = fitz.open(path)
            full_text = ""
            pages_data = []

            # Determiner les pages a traiter
            if page_num is not None:
                pages_to_process = [page_num]
            else:
                pages_to_process = range(len(doc))

            for pnum in pages_to_process:
                page = doc[pnum]
                full_text += page.get_text() + "\n"
                text_dict = page.get_text("dict")
                pages_data.append({
                    "page_num": pnum,
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "blocks": text_dict.get("blocks", []),
                })

            doc.close()
            return full_text, pages_data  # raw, non nettoyé
        except ImportError:
            logger.error("PyMuPDF non installe: pip install pymupdf")
            return "", []
        except Exception as e:
            logger.warning(f"PyMuPDF error: {e}")
            return "", []

    def _extract_ocr(self, path: Path) -> str:
        """OCR avec Tesseract"""
        try:
            import fitz
            from PIL import Image
            import pytesseract

            if self.tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path

            doc = fitz.open(path)
            text = ""
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text += pytesseract.image_to_string(
                    img, lang="fra+eng", config="--oem 3 --psm 6"
                ) + "\n"
            doc.close()
            return self._clean_text(text)
        except ImportError as e:
            logger.error(f"OCR dependances manquantes: {e}")
            return ""
        except Exception as e:
            logger.warning(f"OCR error: {e}")
            return ""

    def _extract_ocr_with_data(self, path: Path, page_num: Optional[int] = None) -> tuple:
        """OCR avec donnees structurelles pour spatial_extractor
        
        Args:
            path: Chemin vers le fichier PDF
            page_num: Numero de page (0-indexed) ou None pour toutes les pages
        """
        try:
            import fitz
            from PIL import Image, ImageEnhance
            import pytesseract
            import re

            if self.tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path

            doc = fitz.open(path)
            full_text = ""
            pages_data = []
            
            # Determiner les pages a traiter
            if page_num is not None:
                pages_to_process = [(page_num, doc[page_num])]
            else:
                pages_to_process = list(enumerate(doc))
            
            for pnum, page in pages_to_process:
                # Convertir en image avec resolution plus elevee (3x = ~300 DPI)
                # Use 4x for very low quality / scanned PDFs
                scale = 4  # Increased from 2 to 4 for better OCR on scanned images
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Preprocessing: convert to grayscale and enhance contrast
                img_gray = img.convert('L')
                
                # Enhance contrast for better OCR on floor plans
                enhancer = ImageEnhance.Contrast(img_gray)
                img_gray = enhancer.enhance(1.5)  # Increase contrast by 50%
                
                # Sharpen the image
                from PIL import ImageFilter
                img_gray = img_gray.filter(ImageFilter.SHARPEN)
                
                # OCR avec donnees de position
                data = pytesseract.image_to_data(
                    img_gray,
                    lang="fra+eng",
                    output_type=pytesseract.Output.DICT
                )
                
                # Extraire les mots avec leurs positions
                words = []
                n_boxes = len(data['text'])
                for i in range(n_boxes):
                    text = data['text'][i].strip()
                    if text:
                        words.append({
                            'text': text,
                            'x0': data['left'][i],
                            'y0': data['top'][i],
                            'x1': data['left'][i] + data['width'][i],
                            'y1': data['top'][i] + data['height'][i],
                        })
                
                # Grouper les mots en lignes
                lines = self._group_words_into_lines(words)
                
                # Ajouter les donnees de la page
                pages_data.append({
                    "page_num": pnum,
                    "width": pix.width,
                    "height": pix.height,
                    "lines": lines,
                })
                
                # Pour le texte complet (full page, psm 3)
                page_text = pytesseract.image_to_string(
                    img_gray,
                    lang="fra+eng",
                    config="--oem 3 --psm 3"
                )
                full_text += page_text + "\n"
                
                # Re-OCR the right column only (summary table) at higher DPI + psm 6
                # This captures rows that psm 3 misses due to mixed layout noise
                try:
                    right_x = int(pix.width * 0.62)
                    img_table = img.crop((right_x, 0, pix.width, pix.height))
                    # Upscale 1.5x for better Tesseract accuracy on small text
                    new_w = int(img_table.width * 1.5)
                    new_h = int(img_table.height * 1.5)
                    img_table = img_table.resize((new_w, new_h))
                    img_table_gray = img_table.convert('L')
                    table_text = pytesseract.image_to_string(
                        img_table_gray,
                        lang="fra+eng",
                        config="--oem 3 --psm 6"
                    )
                    full_text += "\n" + table_text
                except Exception:
                    pass
            
            doc.close()
            return self._clean_text(full_text), pages_data
            
        except ImportError as e:
            logger.error(f"OCR dependances manquantes: {e}")
            return "", []
        except Exception as e:
            logger.warning(f"OCR error: {e}")
            return "", []
    
    def _group_words_into_lines(self, words: list) -> list:
        """Groupe les mots en lignes bas sur y position"""
        if not words:
            return []
        
        # Trier par y puis x
        words_sorted = sorted(words, key=lambda w: (w['y0'], w['x0']))
        
        lines = []
        current_line = []
        current_y = None
        y_threshold = 10  # Tolerance pour la meme ligne
        
        for word in words_sorted:
            if current_y is None:
                current_y = word['y0']
                current_line = [word]
            elif abs(word['y0'] - current_y) <= y_threshold:
                current_line.append(word)
            else:
                # Fin de la ligne - la sauvegarder
                if current_line:
                    # Trier les mots de la ligne par x
                    current_line.sort(key=lambda w: w['x0'])
                    line_text = ' '.join(w['text'] for w in current_line)
                    lines.append({
                        'text': line_text,
                        'y0': current_y,
                        'x0': min(w['x0'] for w in current_line),
                        'x1': max(w['x1'] for w in current_line),
                    })
                current_y = word['y0']
                current_line = [word]
        
        # Ajouter la derniere ligne
        if current_line:
            current_line.sort(key=lambda w: w['x0'])
            line_text = ' '.join(w['text'] for w in current_line)
            lines.append({
                'text': line_text,
                'y0': current_y,
                'x0': min(w['x0'] for w in current_line),
                'x1': max(w['x1'] for w in current_line),
            })
        
        return lines

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        # Convertir virgules françaises → points (avec ET sans espace avant m)
        text = re.sub(r"(\d),(\d{2})\s*m", r"\1.\2 m", text)
        text = re.sub(r"(\d),(\d{2})m", r"\1.\2 m", text)
        return text.strip()