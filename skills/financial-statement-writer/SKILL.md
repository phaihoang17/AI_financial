---
name: financial-statement-writer
description: Soạn hoặc rà soát bộ báo cáo tài chính doanh nghiệp Việt Nam từ tài liệu nguồn, theo cấu trúc B01/B02/B03/B09 và có đối chiếu số liệu. Dùng khi người dùng yêu cầu một bộ BCTC giống báo cáo công bố; không dùng cho câu hỏi chỉ cần một chỉ tiêu.
---

# Viết bộ báo cáo tài chính

Khi Codex soạn lời dẫn hoặc biên tập phần chữ, đọc [system prompt](references/system_prompt.md) và áp dụng nội dung như hướng dẫn của dự án. Nếu sau này có thành phần LLM viết báo cáo riêng, truyền file đó dưới vai trò `system` trong lời gọi model. Prompt chỉ hướng dẫn cách trình bày; số liệu và kiểm tra vẫn do nguồn cùng code quyết định.

## Quy trình

1. Xác định pháp nhân/mã chứng khoán, năm tài chính, phạm vi **hợp nhất** hay **riêng**, và mẫu biểu của báo cáo nguồn. Nếu cần cả hai phạm vi, làm hai bộ tách biệt. Không áp mẫu B01-DN của doanh nghiệp thông thường cho ngân hàng hay công ty chứng khoán.
2. Lấy B01, B02, B03 và B09 từ **cùng báo cáo nguồn**. Trong repo này, dùng `src.statements` và `artifacts/statements-v2` khi có; không dựng cả bộ từ catalog chỉ chứa các chỉ tiêu đã chọn. Ưu tiên đối chiếu bản PDF/scan gốc nếu có. Giữ đường dẫn nguồn, mã báo cáo và trạng thái đọc OCR.
3. Trình bày theo thứ tự B01-DN (bảng cân đối kế toán), B02-DN (kết quả hoạt động kinh doanh), B03-DN (lưu chuyển tiền tệ), B09-DN (thuyết minh). Mỗi bảng giữ tên chỉ tiêu, mã số, tham chiếu thuyết minh, hai cột kỳ và đơn vị đúng như nguồn. Giữ thứ tự dòng, dấu âm/ngoặc, dấu gạch ngang và phương pháp lập lưu chuyển tiền tệ của nguồn. B09 giữ các đoạn và bảng thuyết minh theo thứ tự công bố; không tự viết chính sách kế toán hay số thuyết minh còn thiếu.
4. Chạy các phép đối chiếu kế toán bằng `src.formulas.identities` hoặc dùng kết quả đã lưu trong statement set. Đánh dấu dòng và phép kiểm tra không khớp, thiếu dữ liệu hoặc khác đơn vị; **không sửa số liệu để làm bảng cân**. Đưa kết quả đối chiếu và nguồn vào phụ lục.
5. Trên trang đầu và chân trang, ghi rõ đây là **bản dựng lại từ dữ liệu nguồn**, không phải bản gốc có chữ ký/dấu. Không tạo chữ ký, dấu, ý kiến kiểm toán hay khẳng định tuân thủ pháp lý khi nguồn không có. Nếu nguồn thiếu một biểu, để thiếu và nêu rõ; không tạo biểu giả để đủ bộ.
6. Trước khi giao, kiểm tra PDF mở được, đủ các phần có trong nguồn, năm/phạm vi/đơn vị nhất quán, số và mã dòng truy được về nguồn. Xem trực quan trang đầu, mỗi biểu, một số trang B09 và phụ lục; báo rõ mọi giới hạn OCR hoặc đối chiếu.

## Ranh giới

Skill này hướng dẫn người soạn hoặc Codex; nó không thay thế luồng xuất PDF tất định hiện có và không cho phép LLM tự tính hay điền số. Với báo cáo bị gắn `NOT_RECONCILED`, chỉ giao như bản dựng lại cần rà soát bản gốc.
