import flet as ft

def main(page: ft.Page):
    page.title = "档案馆助手"
    page.bgcolor = "white" # 强制背景色
    
    page.add(
        ft.Text("恭喜！APK 打包成功！", size=30, color="teal", weight=ft.FontWeight.BOLD),
        ft.Text("如果你看到了这行字，说明彻底搞定了！", size=16),
        ft.ElevatedButton("点我试试")
    )
    page.update() # 核心：强制刷新界面，消灭白屏

if __name__ == "__main__":
    ft.run(main) # 新版 Flet 的正确启动方式
