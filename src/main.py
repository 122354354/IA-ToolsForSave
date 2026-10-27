import flet as ft

def main(page: ft.Page):
    page.title = "档案馆助手"
    page.bgcolor = "white"
    page.add(
        ft.Text("终于出来了！", size=30, color="teal", weight=ft.FontWeight.BOLD),
        ft.Text("经典版本渲染成功！", size=16)
    )
    page.update()

if __name__ == "__main__":
    # 0.24.1 版本的标准写法
    ft.app(target=main)
