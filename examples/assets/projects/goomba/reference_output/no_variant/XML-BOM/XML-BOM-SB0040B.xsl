<?xml version="1.0" encoding="utf-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:template match="/">
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Bill of Materials</title>
      </head>
      <body>
        <table style="font:8pt Segoe UI" border="1" cellspacing="0" cellpadding="1" bgcolor="#FFFFFF">
          <thead style="font:bold; background-color:#C0C0C0">
            <tr valign="top">
              <xsl:for-each select="/GRID/COLUMNS/COLUMN">
                <td align="center">
                  <xsl:attribute name="width">
                    <xsl:value-of select="@Width" />
                  </xsl:attribute>
                  <xsl:value-of select="@Caption" />
                </td>
              </xsl:for-each>
            </tr>
          </thead>
          <xsl:for-each select="/GRID/ROWS/ROW">
            <tr valign="top">
              <td align="left">
                <xsl:value-of select="@Comment" />
                <xsl:if test="@Comment[.='']">&#160;</xsl:if>
              </td>
              <td align="left">
                <xsl:value-of select="@Description" />
                <xsl:if test="@Description[.='']">&#160;</xsl:if>
              </td>
              <td align="left">
                <xsl:value-of select="@LibRef" />
                <xsl:if test="@LibRef[.='']">&#160;</xsl:if>
              </td>
              <td align="right">
                <xsl:value-of select="@Quantity" />
                <xsl:if test="@Quantity[.='']">&#160;</xsl:if>
              </td>
              <td align="left">
                <xsl:value-of select="@Designator" />
                <xsl:if test="@Designator[.='']">&#160;</xsl:if>
              </td>
            </tr>
          </xsl:for-each>
        </table>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>